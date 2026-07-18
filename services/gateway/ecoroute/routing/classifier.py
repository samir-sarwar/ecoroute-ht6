from __future__ import annotations

import json
import re

import httpx

from ecoroute.api.schemas import NormalizedRequestFeatures, RouterClassification
from ecoroute.config import Settings


def deterministic_classify(features: NormalizedRequestFeatures) -> RouterClassification:
    text = features.normalized_text.lower()
    required = ["text"]
    if features.has_tools:
        required.append("tools")
    if features.has_multimodal:
        required.append("vision")

    if features.contains_secrets or features.has_tools or features.has_multimodal:
        return RouterClassification(
            complexity="high",
            task_type="tool_workflow" if features.has_tools else "unknown",
            risk="high",
            slm_eligible=False,
            cache_eligible=False,
            required_capabilities=required,
            predicted_output_tokens=256,
            confidence=0.99,
            rationale_code="DETERMINISTIC_SAFETY_ESCALATION",
        )
    if re.search(r"\b(law|legal|sue|lawsuit|lawyer|litigation|regulation)\b", text):
        return RouterClassification(
            complexity="high",
            task_type="legal",
            risk="high",
            slm_eligible=False,
            cache_eligible=False,
            required_capabilities=required,
            predicted_output_tokens=480,
            confidence=0.98,
            rationale_code="LEGAL_INTERPRETATION",
        )
    if re.search(r"\b(medical|diagnos|suicid|dangerous|weapon|emergency)\b", text):
        return RouterClassification(
            complexity="high",
            task_type="safety",
            risk="high",
            slm_eligible=False,
            cache_eligible=False,
            required_capabilities=required,
            predicted_output_tokens=320,
            confidence=0.96,
            rationale_code="SAFETY_CRITICAL",
        )

    personalized = features.contains_pii or features.is_personalized or features.detection_uncertain
    if re.search(r"\b(summarize|summary)\b", text):
        task = "summarization"
        complexity = "medium"
    elif re.search(r"\b(extract|classify|sentiment|issue type)\b", text):
        task = "extraction" if "extract" in text else "classification"
        complexity = "medium"
    elif re.search(r"\b(package|shipping|tracking|order|refund)\b", text) and personalized:
        task = "order_support"
        complexity = "medium"
    elif re.search(r"\b(return|exchange|shipping|refund|final.sale|send back|delivery)\b", text):
        task = "policy_qa"
        complexity = "low"
    else:
        task = "unknown"
        complexity = "high"

    in_domain = task in {"policy_qa", "summarization", "classification", "extraction"}
    confidence = 0.95 if task != "unknown" else 0.55
    return RouterClassification(
        complexity=complexity,
        task_type=task,
        risk="medium" if personalized else ("high" if task == "unknown" else "low"),
        slm_eligible=in_domain and not personalized,
        cache_eligible=task == "policy_qa" and not personalized,
        required_capabilities=required,
        predicted_output_tokens=96 if complexity == "low" else 192,
        confidence=confidence,
        rationale_code=(
            "PUBLIC_POLICY_LOOKUP"
            if task == "policy_qa"
            else "SUPPORTED_DOMAIN_TASK"
            if in_domain
            else "UNKNOWN_TASK"
        ),
    )


async def classify(features: NormalizedRequestFeatures, settings: Settings) -> RouterClassification:
    deterministic = deterministic_classify(features)
    # Deterministic safety rules are authoritative. Do not ask a learned router
    # to overrule tools, multimodal input, secrets, legal, or safety-critical text.
    if deterministic.risk == "high" and deterministic.confidence >= 0.90:
        return deterministic
    if not (
        settings.freesolo_router_base_url
        and settings.freesolo_router_model_id
        and settings.freesolo_api_key
    ):
        return deterministic
    prompt = (
        f"SYSTEM_HASH={features.system_prompt_hash}\nTOOLS={str(features.has_tools).lower()}\n"
        f"MULTIMODAL={str(features.has_multimodal).lower()}\nLANG={features.requested_language}\n"
        f"PII={str(features.contains_pii).lower()}\nSECRET={str(features.contains_secrets).lower()}\n"
        f"PERSONALIZED={str(features.is_personalized).lower()}\nPROMPT={features.redacted_preview}"
    )
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            response = await client.post(
                f"{settings.freesolo_router_base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {settings.freesolo_api_key}"},
                json={
                    "model": settings.freesolo_router_model_id,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0,
                    "response_format": {"type": "json_object"},
                },
            )
            response.raise_for_status()
            raw = response.json()["choices"][0]["message"]["content"]
            value = RouterClassification.model_validate(json.loads(raw))
            if value.confidence < 0.70 or value.task_type == "unknown":
                return RouterClassification.fail_closed("ROUTER_LOW_CONFIDENCE")
            required = list(
                dict.fromkeys([*value.required_capabilities, *deterministic.required_capabilities])
            )
            value.required_capabilities = required
            if (
                features.has_tools
                or features.has_multimodal
                or features.contains_secrets
                or features.contains_pii
                or features.is_personalized
                or features.detection_uncertain
            ):
                value.slm_eligible = False
                value.cache_eligible = False
            if features.contains_pii or features.is_personalized or features.detection_uncertain:
                value.risk = "medium" if value.risk == "low" else value.risk
            return value
    except (httpx.HTTPError, KeyError, ValueError, json.JSONDecodeError):
        return RouterClassification.fail_closed()
