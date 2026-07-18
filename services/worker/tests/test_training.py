import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from ecoroute_worker.freesolo.cli import _command_allowed, _redact, build_command, render_config
from ecoroute_worker.freesolo.state import validate_transition
from ecoroute_worker.gemini.generator import (
    GeminiDatasetGenerator,
    GeneratedExample,
    process_examples,
)

ROOT = Path(__file__).parents[3]


def load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_router_reward_penalizes_unsafe_underclassification() -> None:
    environment = load_module(ROOT / "training/router/environment.py", "router_environment")
    expected = {
        "complexity": "high",
        "task_type": "legal",
        "risk": "high",
        "slm_eligible": False,
        "cache_eligible": False,
        "required_capabilities": ["text"],
        "predicted_output_tokens": 256,
        "confidence": 0.98,
        "rationale_code": "LEGAL_INTERPRETATION",
    }
    safe = environment.score_router_response(json.dumps(expected), expected)
    unsafe = environment.score_router_response(
        json.dumps({**expected, "risk": "low", "slm_eligible": True}), expected
    )
    assert safe > unsafe


def test_support_reward_rejects_fabricated_policy() -> None:
    environment = load_module(ROOT / "training/support-slm/environment.py", "support_environment")
    predicted = json.dumps(
        {
            "answer": "Invented",
            "confidence": 0.99,
            "policy_ids": ["not-real"],
            "needs_human": False,
        }
    )
    assert (
        environment.score_support_response(
            predicted, {"answer": "", "policy_ids": [], "needs_human": True}
        )
        == -1
    )


def test_processing_deduplicates_validates_and_splits_stably() -> None:
    output = json.dumps(
        {
            "answer": "Unused items may be returned within 30 days.",
            "confidence": 0.98,
            "policy_ids": ["returns-30-day"],
            "needs_human": False,
        }
    )
    examples = [
        GeneratedExample(
            input="What is the return window?",
            output=output,
            task_type="policy_qa",
            difficulty="easy",
            policy_ids=["returns-30-day"],
        ),
        GeneratedExample(
            input="  What is the return window? ",
            output=output,
            task_type="policy_qa",
            difficulty="easy",
            policy_ids=["returns-30-day"],
        ),
    ]
    processed, manifest = process_examples(examples, {"returns-30-day"})
    assert len(processed) == 1
    assert len(manifest) == 64
    assert processed[0].split in {"train", "eval", "test"}


def test_freesolo_config_and_commands_are_shell_free() -> None:
    rendered = render_config("id='${FREESOLO_ORG}/router'", {"FREESOLO_ORG": "acme"})
    assert rendered == "id='acme/router'"
    assert build_command("train_dry_run", "rendered.toml") == [
        "flash",
        "train",
        "rendered.toml",
        "--dry-run",
    ]
    with pytest.raises(ValueError):
        render_config("${MISSING}", {})


def test_current_freesolo_cli_surface_is_strictly_allowlisted() -> None:
    expected = {
        "env_push": ["flash", "env", "push", "--name", "router-v1", "training/router"],
        "train_dry_run": ["flash", "train", "config.toml", "--dry-run"],
        "train_cost": ["flash", "train", "config.toml", "--cost"],
        "train_launch": ["flash", "train", "config.toml", "--background"],
        "status": ["flash", "status", "run-1"],
        "log": ["flash", "log", "run-1"],
        "cancel": ["flash", "cancel", "run-1"],
        "deploy_dry_run": ["flash", "deploy", "run-1", "--dry-run"],
        "deploy": ["flash", "deploy", "run-1"],
        "export": [
            "flash",
            "export",
            "--adapter-id",
            "run-1",
            "--repository",
            "org/model",
        ],
    }
    for action, command in expected.items():
        kwargs = (
            {"name": "router-v1"}
            if action == "env_push"
            else {"repository": "org/model"}
            if action == "export"
            else {}
        )
        target = (
            "training/router"
            if action == "env_push"
            else "config.toml"
            if action.startswith("train")
            else "run-1"
        )
        built = build_command(action, target, **kwargs)  # type: ignore[arg-type]
        assert built == command
        assert _command_allowed(built)
    assert not _command_allowed(["flash", "train", "config.toml", "--unknown"])
    assert not _command_allowed(["sh", "-c", "flash train config.toml"])


def test_freesolo_output_redacts_credentials() -> None:
    value = _redact("api_key=secret-value bearer abc.def-123 password: hunter2 sk-abcdefgh123456")
    assert "secret-value" not in value
    assert "abc.def-123" not in value
    assert "hunter2" not in value
    assert "sk-abcdefgh123456" not in value
    assert value.count("[REDACTED]") == 4


@pytest.mark.asyncio
async def test_gemini_structured_generation_retries_once() -> None:
    output = {
        "input": "What is the return window?",
        "output": json.dumps(
            {
                "answer": "Thirty days.",
                "confidence": 0.98,
                "policy_ids": ["returns-30-day"],
                "needs_human": False,
            }
        ),
        "task_type": "policy_qa",
        "difficulty": "easy",
        "policy_ids": ["returns-30-day"],
    }

    class Models:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_content(self, **kwargs):
            del kwargs
            self.calls += 1
            return SimpleNamespace(
                parsed=[] if self.calls == 1 else [output],
                text=None,
            )

    models = Models()
    generator = GeminiDatasetGenerator.__new__(GeminiDatasetGenerator)
    generator.client = SimpleNamespace(aio=SimpleNamespace(models=models))
    generator.model = "gemini-test"
    result = await generator.generate_batch(
        batch_id="batch-1",
        business_profile={"name": "Northstar"},
        policies={"returns-30-day": "Thirty days."},
        count=1,
    )
    assert models.calls == 2
    assert result[0].input == "What is the return window?"


def test_processing_rejects_secrets_and_keeps_paraphrase_groups_in_one_split() -> None:
    output = json.dumps(
        {
            "answer": "Unused items may be returned within 30 days.",
            "confidence": 0.98,
            "policy_ids": ["returns-30-day"],
            "needs_human": False,
        }
    )
    examples = [
        GeneratedExample(
            input="What is the return window?",
            output=output,
            task_type="policy_qa",
            difficulty="easy",
            policy_ids=["returns-30-day"],
            paraphrase_group="return-window",
        ),
        GeneratedExample(
            input="How long do I have to return an unused item?",
            output=output,
            task_type="policy_qa",
            difficulty="medium",
            policy_ids=["returns-30-day"],
            paraphrase_group="return-window",
        ),
        GeneratedExample(
            input="My password=do-not-store-this; can I return it?",
            output=output,
            task_type="policy_qa",
            difficulty="hard",
            policy_ids=["returns-30-day"],
        ),
    ]
    processed, _ = process_examples(examples, {"returns-30-day"})
    assert len(processed) == 2
    assert len({item.split for item in processed}) == 1
    assert all("password" not in item.input.lower() for item in processed)


def test_state_machine_rejects_skips() -> None:
    validate_transition("approved", "validating")
    with pytest.raises(ValueError):
        validate_transition("approved", "training")
