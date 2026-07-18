"""FreeSOLO environment contract for the Northstar support SLM."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from freesolo.datasets.types import TaskExample
from freesolo.environments import EnvironmentSingleTurn, RewardResult

POLICY_DIR = Path(__file__).with_name("policies")
DEFAULT_DATASET_PATH = Path(__file__).parent / "dataset" / "train.jsonl"
KNOWN_POLICY_IDS = {path.stem for path in POLICY_DIR.glob("*.txt")}
PROHIBITED = ("i issued the refund", "i cancelled your order", "refund was processed")


def score_support_response(predicted: str, expected: dict[str, Any]) -> float:
    try:
        value = json.loads(predicted)
    except (json.JSONDecodeError, TypeError):
        return -1.0
    required = {"answer", "confidence", "policy_ids", "needs_human"}
    if not isinstance(value, dict) or set(value) != required:
        return -1.0
    policy_ids = set(value["policy_ids"])
    if not policy_ids.issubset(KNOWN_POLICY_IDS):
        return -1.0
    answer = str(value["answer"]).lower()
    if any(phrase in answer for phrase in PROHIBITED):
        return -1.0
    expected_ids = set(expected.get("policy_ids", []))
    policy_score = 0.30 if policy_ids == expected_ids else 0.0
    factual_terms = set(str(expected.get("answer", "")).lower().split())
    actual_terms = set(answer.split())
    overlap = len(factual_terms & actual_terms) / max(1, len(factual_terms))
    factual_score = 0.30 * min(1.0, overlap * 2)
    escalation_score = (
        0.20 if bool(value["needs_human"]) == bool(expected["needs_human"]) else -0.20
    )
    safety_score = 0.10
    words = len(answer.split())
    tone_score = 0.10 if 3 <= words <= 120 else 0.0
    return max(
        -1.0, min(1.0, policy_score + factual_score + escalation_score + safety_score + tone_score)
    )


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open() as handle:
        return [json.loads(line) for line in handle if line.strip()]


SUPPORT_SYSTEM_PROMPT = """You are the Northstar Outfitters policy assistant.
Use only the fictional policy facts below. Return exactly one JSON object with answer,
confidence, policy_ids, and needs_human. Never claim that an order mutation was performed.

{policies}
""".format(
    policies="\n".join(
        f"{path.stem}: {path.read_text().strip()}" for path in sorted(POLICY_DIR.glob("*.txt"))
    )
)


class SupportEnvironment(EnvironmentSingleTurn):
    dataset = load_jsonl(DEFAULT_DATASET_PATH)

    def build_prompt_messages(self, example: TaskExample, prompt_text: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": SUPPORT_SYSTEM_PROMPT},
            {"role": "user", "content": example.input},
        ]

    def score_response(self, example: TaskExample, response_text: str) -> RewardResult:
        expected = example.output
        if isinstance(expected, str):
            expected = json.loads(expected)
        score = score_support_response(
            response_text, expected if isinstance(expected, dict) else {}
        )
        return RewardResult(score=score, threshold=0.85, success=score >= 0.85)

    def score(self, predicted: str, expected: dict[str, Any]) -> float:
        return score_support_response(predicted, expected)


def load_environment(
    dataset_path: str | None = None, split: str = "train", **kwargs: Any
) -> SupportEnvironment:
    environment = SupportEnvironment()
    path = Path(dataset_path) if dataset_path else DEFAULT_DATASET_PATH.with_name(f"{split}.jsonl")
    environment.dataset = load_jsonl(path)
    return environment
