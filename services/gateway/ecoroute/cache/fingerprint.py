from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from typing import Any

from ecoroute.api.schemas import ChatCompletionRequest, NormalizedRequestFeatures


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def exact_fingerprint(
    workspace_id: uuid.UUID,
    request: ChatCompletionRequest,
    features: NormalizedRequestFeatures,
    namespace_version: int,
) -> str:
    def normalize_text(value: str) -> str:
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value)).strip()

    messages: list[dict[str, Any]] = []
    for message in request.messages:
        content: Any = message.content
        if isinstance(content, str):
            content = normalize_text(content)
        elif isinstance(content, list):
            content = [
                {
                    **part,
                    **(
                        {"text": normalize_text(part["text"])}
                        if isinstance(part.get("text"), str)
                        else {}
                    ),
                }
                for part in content
            ]
        messages.append(
            {
                "role": message.role,
                "content": content,
                "name": message.name,
                "tool_call_id": message.tool_call_id,
            }
        )
    value = {
        "workspace_id": str(workspace_id),
        "logical_model": request.model,
        "normalized_messages": messages,
        "system_prompt_hash": features.system_prompt_hash,
        "tool_schema_hash": features.tool_schema_hash,
        "response_format_hash": features.response_format_hash,
        "temperature": request.temperature,
        "top_p": request.top_p,
        "seed": request.seed,
        "policy_cache_namespace_version": namespace_version,
    }
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()
