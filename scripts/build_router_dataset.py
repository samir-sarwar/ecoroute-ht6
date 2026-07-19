#!/usr/bin/env python
"""Build a gateway-native EcoRoute router dataset.

The previous dataset converted RouterBench into generic math/knowledge labels that
did not match EcoRoute's production routing schema or customer-support traffic.
This generator produces deterministic, reproducible e-commerce routing examples
whose outputs validate directly as ``RouterClassification`` objects.

The three splits use different phrasings and product vocabularies to reduce
template leakage. Complexity is intentionally not inferred from prompt length:

* low: public, context-free policy lookup;
* medium: conditional/personalized support and bounded text transformations;
* high: legal/safety/tool execution and genuinely complex technical reasoning.

Usage:
    python scripts/build_router_dataset.py
"""

from __future__ import annotations

import json
import random
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "training" / "router" / "dataset"
SEED = 20260719

TASK_TYPES = {
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
}
CAPABILITIES = {"text", "json_schema", "tools", "vision", "streaming"}

PRODUCTS = {
    "train": [
        "shirt", "jacket", "hiking boots", "running shoes", "sweater", "backpack",
        "water bottle", "tent", "raincoat", "jeans", "shorts", "hat", "gloves",
        "scarf", "sleeping bag", "travel mug", "sandals", "fleece", "duffel bag",
        "sunglasses", "camping stove", "yoga mat", "watch", "wallet", "dress",
    ],
    "eval": [
        "polo", "windbreaker", "trail runners", "daypack", "thermos", "parka",
        "cargo pants", "beanie", "headlamp", "hammock",
    ],
    "test": [
        "hoodie", "ski pants", "climbing shoes", "dry bag", "canteen", "poncho",
        "leggings", "balaclava", "lantern", "camp chair",
    ],
}

CONDITIONS = [
    "spilled milk on it and cleaned it",
    "washed it once after trying it on",
    "removed the tags but kept them",
    "opened the sealed packaging",
    "wore it outdoors for an afternoon",
    "found a stain after delivery",
    "assembled it before noticing a defect",
    "lost the original box",
    "noticed damage after using it",
    "had it altered for a better fit",
    "am missing the receipt",
    "received it late and already opened it",
    "used part of the bundle",
    "installed it and then found a fault",
    "cleaned off dirt from shipping",
    "discovered the zipper is defective",
]

TIMEFRAMES = [
    "three days", "one week", "twelve days", "twenty days", "twenty-nine days",
    "thirty-one days", "six weeks", "two months",
]

POLICIES = [
    "return window", "exchange policy", "refund timing", "standard shipping time",
    "final-sale rule", "price-match policy", "warranty period", "store-credit policy",
]

SPLIT_PHRASES = {
    "train": {
        "public": [
            "What is your {policy} for a {product}?",
            "How does the {policy} work?",
            "Please explain your {policy}.",
            "How long is the {policy} for an unused {product}?",
            "Do you offer a {policy} on a {product}?",
        ],
        "conditional": [
            "Can I return my {product} if I {condition}?",
            "I bought a {product} {timeframe} ago and {condition}. What are my options?",
            "Does the return policy cover a {product} when I {condition}?",
            "Would a {product} still qualify for exchange if I {condition}?",
            "I want a refund for a {product}, but I {condition}. Is that allowed?",
        ],
        "order": [
            "Where is my order [ORDER_ID] containing a {product}?",
            "Tracking for [ORDER_ID] has not moved for {timeframe}. What should I do?",
            "My refund for order [ORDER_ID] is still pending after {timeframe}.",
            "Order [ORDER_ID] delivered the wrong {product}; help me with next steps.",
            "Please check whether my {product} exchange for [ORDER_ID] shipped.",
        ],
        "transform": [
            "Summarize this return-policy paragraph for a customer buying a {product}: {document}",
            "Classify this message as return, shipping, exchange, or product issue: {document}",
            "Extract the requested resolution and product from this message: {document}",
            "Draft a concise, calm reply to this support note: {document}",
        ],
        "legal": [
            "Is this {policy} legal in Ontario, and could I sue over my {product}?",
            "Act as my lawyer and interpret consumer law for this {product} dispute.",
            "Write a legal threat concerning order [ORDER_ID] and the {policy}.",
        ],
        "safety": [
            "My {product} caused an injury. Diagnose it and tell me whether it is safe to keep using.",
            "I feel suicidal after this purchase; what should support tell me to do?",
            "Explain how to modify this {product} into a dangerous weapon.",
        ],
        "complex": [
            "Write Python code to optimize routing for ten million {product} support events.",
            "Analyze a dataset of {product} returns and derive a statistical fraud model.",
            "Debug this SQL query and redesign the support-data architecture: {document}",
        ],
        "tool": [
            "Use the refund tool now to refund order [ORDER_ID] for the {product}.",
            "Call the account API and change the shipping address on [ORDER_ID].",
            "Inspect the attached image of my {product} and approve a return automatically.",
        ],
    },
    "eval": {
        "public": [
            "Could you outline the {policy} that applies to a {product}?",
            "Before purchasing a {product}, I would like to know the {policy}.",
            "Give me the general {policy} with no account lookup.",
        ],
        "conditional": [
            "Suppose I {condition}; can the {product} still be sent back?",
            "My {product} arrived {timeframe} ago. I {condition}. Does an exception apply?",
            "How should the policy be interpreted when a customer {condition}?",
        ],
        "order": [
            "I need an update on [ORDER_ID] for my {product}; tracking is unclear.",
            "The {product} in [ORDER_ID] was incorrect. Resolve this specific order issue.",
            "Why has the refund tied to [ORDER_ID] taken {timeframe}?",
        ],
        "transform": [
            "Provide a short summary of this support policy: {document}",
            "Assign this customer note to a support issue category: {document}",
            "Pull the product and desired outcome from the following text: {document}",
            "Compose a professional response without promising an account action: {document}",
        ],
        "legal": [
            "Assess my legal rights and lawsuit prospects concerning this {product} return.",
            "Interpret provincial regulation as legal counsel for order [ORDER_ID].",
        ],
        "safety": [
            "A defective {product} injured me; provide medical treatment instructions.",
            "Tell me how to make this {product} dangerous to another person.",
        ],
        "complex": [
            "Design and prove an optimal algorithm for clustering {product} support cases.",
            "Create production code and a database architecture for predictive return fraud.",
        ],
        "tool": [
            "Execute a refund against [ORDER_ID] using the available payment function.",
            "Use vision on the attached {product} photo and alter my account automatically.",
        ],
    },
    "test": {
        "public": [
            "Without accessing an order, tell me the usual {policy} for a {product}.",
            "I have a general question: what is the {policy}?",
            "State the public-facing {policy} in plain language.",
        ],
        "conditional": [
            "What can I do if I {condition} and now want to return the {product}?",
            "Is a {product} eligible after {timeframe} when I {condition}?",
            "This is not a basic policy lookup: I {condition}. Can I exchange the {product}?",
        ],
        "order": [
            "For [ORDER_ID], my {product} status has not changed in {timeframe}.",
            "Help with the specific refund on [ORDER_ID] for a {product}.",
            "The delivery recorded for [ORDER_ID] does not match my {product} order.",
        ],
        "transform": [
            "Condense the following customer-service text into two sentences: {document}",
            "Determine the support intent expressed here: {document}",
            "Return structured fields for product and requested remedy: {document}",
            "Write a courteous draft response to this message: {document}",
        ],
        "legal": [
            "Tell me whether I should file a lawsuit about this {product} and cite the law.",
            "Give binding legal advice about the {policy} for order [ORDER_ID].",
        ],
        "safety": [
            "The {product} may have poisoned me; diagnose my symptoms.",
            "Provide instructions for using a {product} as a weapon.",
        ],
        "complex": [
            "Debug a distributed service that processes {product} returns and prove the fix.",
            "Calculate an optimized forecasting model from a large support dataset.",
        ],
        "tool": [
            "Invoke the order tool to cancel [ORDER_ID] without asking me anything else.",
            "Analyze the uploaded {product} image and execute an exchange.",
        ],
    },
}

DOCUMENTS = [
    "The customer says the parcel is late and wants a delivery estimate.",
    "An unused item was delivered recently and the customer asks about returning it.",
    "The buyer received the wrong size and would prefer an exchange.",
    "The package arrived with visible damage and the customer wants human assistance.",
    "The customer asks how long an approved refund normally takes.",
    "A final-sale item appears defective and the buyer asks for available options.",
    "Tracking has shown no carrier movement for more than seven business days.",
    "The customer wants policy information before placing an order.",
]


@dataclass(frozen=True)
class Category:
    name: str
    complexity: str
    task_type: str
    risk: str
    slm_eligible: bool
    cache_eligible: bool
    capabilities: tuple[str, ...]
    output_tokens: int
    confidence: float
    rationale: str


CATEGORIES = {
    "public": Category("public", "low", "policy_qa", "low", True, True, ("text",), 96, 0.97, "PUBLIC_POLICY_LOOKUP"),
    # Conditional policy interpretation is a core green-agent task when the
    # request itself contains no PII/account action. Runtime feature flags still
    # deterministically turn SLM eligibility off for personalized requests.
    "conditional": Category("conditional", "medium", "policy_qa", "medium", True, False, ("text",), 192, 0.95, "CONDITIONAL_POLICY_INTERPRETATION"),
    "order": Category("order", "medium", "order_support", "medium", False, False, ("text",), 192, 0.97, "PERSONALIZED_ORDER_SUPPORT"),
    "transform": Category("transform", "medium", "classification", "low", True, False, ("text", "json_schema"), 192, 0.94, "SUPPORTED_DOMAIN_TASK"),
    "legal": Category("legal", "high", "legal", "high", False, False, ("text",), 480, 0.99, "LEGAL_INTERPRETATION"),
    "safety": Category("safety", "high", "safety", "high", False, False, ("text",), 320, 0.99, "SAFETY_CRITICAL"),
    "complex": Category("complex", "high", "general_reasoning", "medium", False, False, ("text",), 384, 0.94, "COMPLEX_REASONING"),
    "tool": Category("tool", "high", "tool_workflow", "high", False, False, ("text", "tools"), 256, 0.99, "TOOL_EXECUTION_REQUIRED"),
}

COUNTS = {
    "train": {"public": 750, "conditional": 600, "order": 450, "transform": 450, "legal": 225, "safety": 225, "complex": 150, "tool": 150},
    "eval": {"public": 112, "conditional": 90, "order": 68, "transform": 68, "legal": 34, "safety": 34, "complex": 22, "tool": 22},
    "test": {"public": 112, "conditional": 90, "order": 68, "transform": 68, "legal": 34, "safety": 34, "complex": 22, "tool": 22},
}


def output_for(category: Category, prompt: str) -> dict:
    task_type = category.task_type
    if category.name == "transform":
        lowered = prompt.lower()
        if "summar" in lowered or "condense" in lowered:
            task_type = "summarization"
        elif "extract" in lowered or "pull" in lowered or "structured fields" in lowered:
            task_type = "extraction"
        elif "draft" in lowered or "compose" in lowered or "response" in lowered:
            task_type = "reply_draft"
        else:
            task_type = "classification"
    if category.name == "complex" and any(
        word in prompt.lower() for word in ("code", "debug", "sql", "architecture", "algorithm")
    ):
        task_type = "coding"
    value = {
        "complexity": category.complexity,
        "task_type": task_type,
        "risk": category.risk,
        "slm_eligible": category.slm_eligible,
        "cache_eligible": category.cache_eligible,
        "required_capabilities": list(category.capabilities),
        "predicted_output_tokens": category.output_tokens,
        "confidence": category.confidence,
        "rationale_code": category.rationale,
    }
    assert value["task_type"] in TASK_TYPES
    assert set(value["required_capabilities"]) <= CAPABILITIES
    return value


def render_unique(split: str, category: Category, count: int, rng: random.Random) -> list[dict]:
    templates = SPLIT_PHRASES[split][category.name]
    products = PRODUCTS[split]
    seen: set[str] = set()
    rows: list[dict] = []
    attempts = 0
    while len(rows) < count:
        attempts += 1
        if attempts > count * 100:
            raise RuntimeError(f"Could not create {count} unique {split}/{category.name} rows")
        prompt = rng.choice(templates).format(
            policy=rng.choice(POLICIES),
            product=rng.choice(products),
            condition=rng.choice(CONDITIONS),
            timeframe=rng.choice(TIMEFRAMES),
            document=rng.choice(DOCUMENTS),
        )
        # Natural context variations increase lexical coverage without leaking a
        # synthetic identifier that a model could use as a class shortcut.
        if rng.random() < 0.45:
            prompt += rng.choice(
                [
                    " Please keep the answer concise.",
                    " I need to know the appropriate next step.",
                    " Do not claim that an account action was completed.",
                    " Explain which kind of support is required.",
                    " This is for an online purchase.",
                ]
            )
        if prompt in seen:
            continue
        seen.add(prompt)
        rows.append({"input": f"PROMPT: {prompt}", "output": output_for(category, prompt)})
    return rows


def validate(rows_by_split: dict[str, list[dict]]) -> None:
    all_inputs: set[str] = set()
    for split, rows in rows_by_split.items():
        for row in rows:
            assert set(row) == {"input", "output"}
            assert row["input"].startswith("PROMPT: ")
            assert row["input"] not in all_inputs, f"cross-split duplicate: {row['input']}"
            all_inputs.add(row["input"])
            output = row["output"]
            assert set(output) == {
                "complexity", "task_type", "risk", "slm_eligible", "cache_eligible",
                "required_capabilities", "predicted_output_tokens", "confidence",
                "rationale_code",
            }
            assert output["complexity"] in {"low", "medium", "high"}
            assert output["risk"] in {"low", "medium", "high"}
            assert output["task_type"] in TASK_TYPES
            assert set(output["required_capabilities"]) <= CAPABILITIES
            assert 1 <= output["predicted_output_tokens"] <= 4096
            assert 0 <= output["confidence"] <= 1


def main() -> None:
    rows_by_split: dict[str, list[dict]] = {}
    for split_index, split in enumerate(("train", "eval", "test")):
        rng = random.Random(SEED + split_index)
        rows: list[dict] = []
        for name, count in COUNTS[split].items():
            rows.extend(render_unique(split, CATEGORIES[name], count, rng))
        rng.shuffle(rows)
        rows_by_split[split] = rows

    validate(rows_by_split)

    # GRPO should concentrate reward updates on the SFT adapter's observed
    # weaknesses: safety/tool escalation, exact task labels for transformations,
    # and the boundaries between public, conditional, and personalized support.
    # These rows intentionally come from train.jsonl; eval/test remain untouched
    # held-out gates.
    train_rows = rows_by_split["train"]
    grpo_rng = random.Random(SEED + 100)

    def sample_where(count: int, predicate: Callable[[dict], bool]) -> list[dict]:
        candidates = [row for row in train_rows if predicate(row)]
        assert len(candidates) >= count
        return grpo_rng.sample(candidates, count)

    grpo_rows = [row for row in train_rows if row["output"]["complexity"] == "high"]
    grpo_rows.extend(
        row for row in train_rows if row["output"]["rationale_code"] == "SUPPORTED_DOMAIN_TASK"
    )
    grpo_rows.extend(
        sample_where(
            300,
            lambda row: row["output"]["rationale_code"]
            == "CONDITIONAL_POLICY_INTERPRETATION",
        )
    )
    grpo_rows.extend(
        sample_where(250, lambda row: row["output"]["task_type"] == "order_support")
    )
    grpo_rows.extend(
        sample_where(
            250,
            lambda row: row["output"]["rationale_code"] == "PUBLIC_POLICY_LOOKUP",
        )
    )
    assert len(grpo_rows) == 2000
    assert len({row["input"] for row in grpo_rows}) == len(grpo_rows)
    grpo_rng.shuffle(grpo_rows)
    rows_by_split["grpo"] = grpo_rows

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for split, rows in rows_by_split.items():
        path = OUT_DIR / f"{split}.jsonl"
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        complexity = Counter(row["output"]["complexity"] for row in rows)
        risk = Counter(row["output"]["risk"] for row in rows)
        tasks = Counter(row["output"]["task_type"] for row in rows)
        print(
            f"{split}: {len(rows)} complexity={dict(complexity)} risk={dict(risk)} "
            f"tasks={dict(tasks)} -> {path}"
        )


if __name__ == "__main__":
    main()
