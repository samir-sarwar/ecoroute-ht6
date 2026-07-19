from __future__ import annotations

import ast
import json
from collections import Counter
from pathlib import Path

from ecoroute.api.schemas import RouterClassification
from ecoroute.routing.classifier import ROUTER_SYSTEM_PROMPT

ROOT = Path(__file__).resolve().parents[4]
DATASET = ROOT / "training" / "router" / "dataset"


def _rows(split: str) -> list[dict]:
    return [
        json.loads(line)
        for line in (DATASET / f"{split}.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _environment_system_prompt() -> str:
    module = ast.parse((ROOT / "training" / "router" / "environment.py").read_text("utf-8"))
    for node in module.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "SYSTEM_PROMPT" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("training router SYSTEM_PROMPT was not found")


def test_every_router_label_uses_the_gateway_schema() -> None:
    for split in ("train", "eval", "test", "grpo"):
        rows = _rows(split)
        assert rows
        for row in rows:
            parsed = RouterClassification.model_validate(row["output"])
            assert parsed.classification_source == "deterministic"


def test_router_splits_are_disjoint_and_have_expected_sizes() -> None:
    expected_sizes = {"train": 3000, "eval": 450, "test": 450}
    seen: set[str] = set()
    for split, expected_size in expected_sizes.items():
        rows = _rows(split)
        assert len(rows) == expected_size
        prompts = {row["input"] for row in rows}
        assert len(prompts) == len(rows)
        assert prompts.isdisjoint(seen)
        seen.update(prompts)


def test_grpo_curriculum_has_expected_size_and_focus() -> None:
    rows = _rows("grpo")
    train_inputs = {row["input"] for row in _rows("train")}
    assert len(rows) == 2000
    assert len({row["input"] for row in rows}) == len(rows)
    assert all(row["input"] in train_inputs for row in rows)
    rationale = Counter(row["output"]["rationale_code"] for row in rows)
    assert rationale["SAFETY_CRITICAL"] > 0
    assert rationale["TOOL_EXECUTION_REQUIRED"] > 0
    assert rationale["SUPPORTED_DOMAIN_TASK"] == 450


def test_router_dataset_covers_all_routing_tiers_and_safety_cases() -> None:
    for split in ("train", "eval", "test"):
        rows = _rows(split)
        complexity = Counter(row["output"]["complexity"] for row in rows)
        risk = Counter(row["output"]["risk"] for row in rows)
        tasks = Counter(row["output"]["task_type"] for row in rows)
        assert set(complexity) == {"low", "medium", "high"}
        assert set(risk) == {"low", "medium", "high"}
        assert complexity["medium"] > complexity["low"]
        assert complexity["medium"] > complexity["high"]
        assert tasks["policy_qa"] > 0
        assert tasks["order_support"] > 0
        assert tasks["legal"] > 0
        assert tasks["safety"] > 0
        assert tasks["tool_workflow"] > 0


def test_training_and_runtime_router_prompts_are_identical() -> None:
    assert _environment_system_prompt() == ROUTER_SYSTEM_PROMPT
