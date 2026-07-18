from __future__ import annotations

import json
import re
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ecoroute.api.schemas import QualityVerdict, RouterClassification

KNOWN_POLICY_IDS = {
    "returns-30-day",
    "final-sale",
    "exchange-stock",
    "shipping-standard",
    "shipping-delay",
    "refund-timing",
}
PROHIBITED_PROMISES = (
    "i have issued the refund",
    "your refund has been processed",
    "i cancelled your order",
    "i changed your order",
)


def verify_output(
    raw: str,
    classification: RouterClassification,
    *,
    specialized: bool,
    force_failure: bool = False,
    response_format: dict[str, Any] | None = None,
    maximum_characters: int = 32_000,
    minimum_support_confidence: float = 0.80,
) -> QualityVerdict:
    if force_failure:
        return QualityVerdict(passed=False, reason="demo_forced_failure")
    if not raw.strip():
        return QualityVerdict(passed=False, reason="empty_output")
    if len(raw) > maximum_characters:
        return QualityVerdict(passed=False, reason="output_too_long")
    if re.search(
        r"(?i)(sk-[a-z0-9_-]{8,}|AIza[a-z0-9_-]{8,}|-----BEGIN .*PRIVATE KEY-----)",
        raw,
    ):
        return QualityVerdict(passed=False, reason="possible_secret_echo")
    if any(phrase in raw.lower() for phrase in PROHIBITED_PROMISES):
        return QualityVerdict(passed=False, reason="prohibited_promise")
    if response_format and response_format.get("type") in {"json_object", "json_schema"}:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return QualityVerdict(passed=False, reason="invalid_requested_json")
        if response_format.get("type") == "json_schema":
            json_schema = response_format.get("json_schema")
            schema = (
                json_schema.get("schema")
                if isinstance(json_schema, dict)
                else response_format.get("schema")
            )
            if not isinstance(schema, dict):
                return QualityVerdict(passed=False, reason="missing_requested_json_schema")
            try:
                Draft202012Validator.check_schema(schema)
                Draft202012Validator(schema).validate(value)
            except SchemaError:
                return QualityVerdict(passed=False, reason="invalid_requested_json_schema")
            except ValidationError:
                return QualityVerdict(passed=False, reason="json_schema_validation_failed")
    if not specialized:
        return QualityVerdict(passed=True, reason="deterministic_checks_passed", answer=raw)
    try:
        value = json.loads(raw)
        answer = str(value["answer"])
        confidence = float(value["confidence"])
        policy_ids = [str(item) for item in value["policy_ids"]]
        needs_human = bool(value["needs_human"])
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return QualityVerdict(passed=False, reason="invalid_support_contract")
    if confidence < minimum_support_confidence:
        return QualityVerdict(passed=False, reason="low_support_confidence", confidence=confidence)
    if needs_human:
        return QualityVerdict(
            passed=False, reason="human_escalation_required", confidence=confidence
        )
    if not set(policy_ids).issubset(KNOWN_POLICY_IDS):
        return QualityVerdict(passed=False, reason="unknown_policy_id", policy_ids=policy_ids)
    if classification.task_type not in {
        "policy_qa",
        "summarization",
        "classification",
        "extraction",
    }:
        return QualityVerdict(passed=False, reason="task_outside_slm_allowlist")
    return QualityVerdict(
        passed=True,
        reason="support_contract_passed",
        confidence=confidence,
        policy_ids=policy_ids,
        answer=answer,
    )
