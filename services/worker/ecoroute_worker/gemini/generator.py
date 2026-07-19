from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Literal

from ecoroute.cache.embeddings import cosine_similarity, get_local_embedder
from ecoroute.config import get_settings
from google import genai
from google.genai import types
from pydantic import BaseModel, Field, RootModel, ValidationError

TaskType = Literal[
    "policy_qa",
    "order_support",
    "summarization",
    "classification",
    "extraction",
    "reply_draft",
    "tool_workflow",
    "legal",
    "safety",
    "coding",
    "general_reasoning",
    "unknown",
]


class GeneratedExample(BaseModel):
    input: str = Field(min_length=3, max_length=4000)
    output: str
    task_type: TaskType
    difficulty: Literal["easy", "medium", "hard"]
    policy_ids: list[str]
    adversarial: bool = False
    paraphrase_group: str | None = None


class GenerationBatch(RootModel[list[GeneratedExample]]):
    pass


@dataclass(frozen=True)
class ProcessedExample:
    external_id: str
    split: str
    input: str
    output: dict[str, Any]
    metadata: dict[str, Any]
    embedding: list[float]


class GeminiDatasetGenerator:
    def __init__(self, api_key: str, model: str) -> None:
        if not api_key:
            raise ValueError("GEMINI_API_KEY is not configured")
        self.client = genai.Client(api_key=api_key)
        self.model = model

    async def generate_batch(
        self,
        *,
        batch_id: str,
        business_profile: dict[str, Any],
        policies: dict[str, str],
        count: int,
        distribution: dict[str, int] | None = None,
    ) -> list[GeneratedExample]:
        if not 1 <= count <= 50:
            raise ValueError("Gemini batches must contain between 1 and 50 examples")
        instruction = f"""You generate reviewed training candidates for a support model.
Batch ID: {batch_id}
Business profile: {json.dumps(business_profile, sort_keys=True)}
Fictional policy facts: {json.dumps(policies, sort_keys=True)}
Allowed policy_ids: {json.dumps(sorted(policies), sort_keys=True)}
Requested distribution: {json.dumps(distribution or {}, sort_keys=True)}
Create exactly {count} diverse examples. Use the Batch ID as a randomness seed, but do not
mention it in customer-facing text. Every input must be distinct from generic FAQ wording and
should vary item types, customer wording, urgency, ambiguity, channel style, and missing context.
Use only supplied policy facts. Include normal, paraphrased, incomplete, adversarial, and
out-of-domain inputs as requested. policy_ids must be exact values from Allowed policy_ids, or []
for out-of-domain examples. The top-level policy_ids and the JSON string's policy_ids must match.
The output field must itself be a JSON string with answer, confidence, policy_ids, and needs_human.
Out-of-domain examples must set needs_human=true and must not invent an answer.
"""
        last_error: Exception | None = None
        for _ in range(2):
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=instruction,
                    config=types.GenerateContentConfig(
                        temperature=0.8,
                        response_mime_type="application/json",
                        response_schema=GenerationBatch,
                    ),
                )
                if response.parsed is not None:
                    parsed = GenerationBatch.model_validate(response.parsed)
                else:
                    parsed = GenerationBatch.model_validate_json(response.text or "[]")
                if len(parsed.root) != count:
                    raise ValueError(f"expected {count} examples, received {len(parsed.root)}")
                return parsed.root
            except (ValidationError, ValueError, TypeError) as exc:
                last_error = exc
        raise RuntimeError(
            f"Gemini returned invalid structured output twice: {type(last_error).__name__}"
        )


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()


def process_examples(
    examples: list[GeneratedExample],
    known_policy_ids: set[str],
    requested_distribution: dict[str, Any] | None = None,
) -> tuple[list[ProcessedExample], str]:
    settings = get_settings()
    embedder = get_local_embedder(settings.embedding_model, settings.use_sentence_transformers)
    seen: set[str] = set()
    accepted: list[tuple[GeneratedExample, dict[str, Any], list[float]]] = []
    for example in examples:
        input_text = _normalize(example.input)
        normalized_key = input_text.casefold()
        if normalized_key in seen:
            continue
        if not set(example.policy_ids).issubset(known_policy_ids):
            continue
        if re.search(
            r"(?i)(sk-[a-z0-9_-]{6,}|-----BEGIN .*PRIVATE KEY-----|password\s*=)", input_text
        ):
            continue
        try:
            output = json.loads(example.output)
        except json.JSONDecodeError:
            continue
        if set(output) != {"answer", "confidence", "policy_ids", "needs_human"}:
            continue
        if (
            not isinstance(output["answer"], str)
            or not isinstance(output["confidence"], (int, float))
            or not 0 <= float(output["confidence"]) <= 1
            or not isinstance(output["policy_ids"], list)
            or not isinstance(output["needs_human"], bool)
            or not set(output["policy_ids"]).issubset(known_policy_ids)
        ):
            continue
        embedding = embedder.encode(input_text)
        if not example.paraphrase_group and any(
            cosine_similarity(embedding, prior_embedding) > 0.97
            for _, _, prior_embedding in accepted
        ):
            continue
        seen.add(normalized_key)
        accepted.append((example.model_copy(update={"input": input_text}), output, embedding))

    distribution = requested_distribution or {}
    task_targets = distribution.get("taskType", distribution.get("task_type", {}))
    difficulty_targets = distribution.get("difficulty", {})
    if isinstance(task_targets, dict) or isinstance(difficulty_targets, dict):
        # Deterministically interleave categories so early truncation cannot collapse diversity.
        accepted.sort(
            key=lambda item: (
                sum(1 for value, _, _ in accepted if value.task_type == item[0].task_type)
                / max(1, int(task_targets.get(item[0].task_type, 1)))
                if isinstance(task_targets, dict)
                else 0,
                sum(1 for value, _, _ in accepted if value.difficulty == item[0].difficulty)
                / max(1, int(difficulty_targets.get(item[0].difficulty, 1)))
                if isinstance(difficulty_targets, dict)
                else 0,
                item[0].task_type,
                item[0].difficulty,
                item[0].input,
            )
        )

    # Stable split by paraphrase group (or normalized input), so related examples never leak.
    processed: list[ProcessedExample] = []
    canonical_rows: list[dict[str, Any]] = []
    for example, output, embedding in sorted(accepted, key=lambda item: item[0].input):
        group = example.paraphrase_group or example.input.casefold()
        bucket = int(hashlib.sha256(group.encode()).hexdigest()[:8], 16) % 100
        split = "train" if bucket < 70 else "eval" if bucket < 85 else "test"
        external_id = (
            "support_"
            + hashlib.sha256(f"{group}\0{example.input.casefold()}".encode()).hexdigest()[:16]
        )
        metadata = {
            "id": external_id,
            "task_type": example.task_type,
            "difficulty": example.difficulty,
            "policy_ids": example.policy_ids,
            "adversarial": example.adversarial,
            "paraphrase_group": example.paraphrase_group,
            "source": "gemini_synthetic",
            "approved": False,
        }
        processed.append(
            ProcessedExample(
                external_id=external_id,
                split=split,
                input=example.input,
                output=output,
                metadata=metadata,
                embedding=embedding,
            )
        )
        canonical_rows.append(
            {"input": example.input, "output": output, "metadata": metadata, "split": split}
        )
    manifest = hashlib.sha256(
        "\n".join(
            json.dumps(row, sort_keys=True, separators=(",", ":")) for row in canonical_rows
        ).encode()
    ).hexdigest()
    return processed, manifest
