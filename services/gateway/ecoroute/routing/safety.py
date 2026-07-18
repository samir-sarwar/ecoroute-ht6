from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid

from ecoroute.api.schemas import ChatCompletionRequest, NormalizedRequestFeatures

EMAIL = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
PHONE = re.compile(r"(?<!\d)(?:\+?1[ .-]?)?(?:\(?\d{3}\)?[ .-]?)\d{3}[ .-]?\d{4}(?!\d)")
CARD = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
API_KEY = re.compile(
    r"(?i)(?:sk-[a-z0-9_-]{6,}|AIza[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{8,}|bearer\s+[a-z0-9._~+/-]{8,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
)
SECRET_ASSIGNMENT = re.compile(r"(?i)\b(?:password|passwd|secret|api[_ -]?key|token)\s*[:=]\s*\S+")
ORDER_ID = re.compile(
    r"(?i)\b(?:order|account|reference|tracking)\s*(?:number|no\.?|#|id)?\s*[:#-]?\s*[A-Z0-9-]{4,}\b"
)
ADDRESS = re.compile(
    r"(?i)\b\d{1,6}\s+[A-Z][A-Za-z.' -]+\s+(?:street|st|road|rd|avenue|ave|drive|dr|lane|ln|boulevard|blvd)\b"
)
PERSONAL_CONTEXT = re.compile(
    r"(?i)\b(my order|my account|i ordered|i bought|ship(?:ped)? to me|my package|my refund|my card)\b"
)


def stable_hash(value: object | None) -> str | None:
    if value is None:
        return None
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode()).hexdigest()


def luhn_valid(candidate: str) -> bool:
    digits = [int(char) for char in candidate if char.isdigit()]
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def redact(text: str) -> tuple[str, bool, bool, bool, bool]:
    pii = False
    secrets = False
    personalized = False
    uncertain = False
    result = text
    if EMAIL.search(result):
        pii = True
        result = EMAIL.sub("[EMAIL]", result)
    if PHONE.search(result):
        pii = True
        result = PHONE.sub("[PHONE]", result)
    for match in list(CARD.finditer(result)):
        if luhn_valid(match.group()):
            pii = True
            result = result.replace(match.group(), "[CARD]")
        else:
            uncertain = True
    if API_KEY.search(result) or SECRET_ASSIGNMENT.search(result):
        secrets = True
        result = API_KEY.sub("[API_KEY]", result)
        result = SECRET_ASSIGNMENT.sub("[SECRET]", result)
    if ADDRESS.search(result):
        pii = True
        personalized = True
        result = ADDRESS.sub("[ADDRESS]", result)
    if ORDER_ID.search(result):
        personalized = True
        result = ORDER_ID.sub("[ORDER_ID]", result)
    if PERSONAL_CONTEXT.search(result):
        personalized = True
    return result, pii, secrets, personalized, uncertain


def normalize_request(
    request_id: uuid.UUID, request: ChatCompletionRequest
) -> NormalizedRequestFeatures:
    normalized_messages: list[dict[str, str]] = []
    user_indices: list[int] = []
    has_multimodal = False
    for message in request.messages:
        text = unicodedata.normalize("NFKC", message.text()).strip()
        text = re.sub(r"\s+", " ", text)
        normalized_messages.append({"role": message.role, "content": text})
        if message.role == "user":
            user_indices.append(len(normalized_messages) - 1)
        has_multimodal = has_multimodal or message.is_multimodal()

    final_user_index = user_indices[-1] if user_indices else len(normalized_messages)
    normalized_text = normalized_messages[final_user_index]["content"] if user_indices else ""
    context_messages = [
        message for index, message in enumerate(normalized_messages) if index != final_user_index
    ]

    # Embed/classify only the current user turn, while binding every other turn into
    # the context hash. This prevents cross-conversation semantic answer reuse.
    redacted, pii, secrets, personalized, uncertain = redact(normalized_text)
    for index, context_message in enumerate(normalized_messages):
        if index == final_user_index:
            continue
        _, turn_pii, turn_secrets, turn_personalized, turn_uncertain = redact(
            context_message["content"]
        )
        pii = pii or turn_pii
        secrets = secrets or turn_secrets
        personalized = personalized or turn_personalized
        uncertain = uncertain or turn_uncertain
    language = "en"
    if re.search(r"[\u4e00-\u9fff]", normalized_text):
        language = "zh"
    elif re.search(r"[\u0600-\u06ff]", normalized_text):
        language = "ar"
    input_tokens = max(1, (sum(len(item["content"]) for item in normalized_messages) + 3) // 4)
    return NormalizedRequestFeatures(
        request_id=request_id,
        logical_model=request.model,
        normalized_text=normalized_text,
        system_prompt_hash=stable_hash(context_messages) or hashlib.sha256(b"").hexdigest(),
        tool_schema_hash=stable_hash(request.tools),
        response_format_hash=stable_hash(request.response_format),
        message_count=len(request.messages),
        assistant_turn_count=sum(message.role == "assistant" for message in request.messages),
        input_token_estimate=input_tokens,
        has_tools=bool(request.tools),
        has_multimodal=has_multimodal,
        contains_pii=pii,
        contains_secrets=secrets,
        is_personalized=personalized,
        deterministic=request.temperature in {None, 0},
        requested_language=language,
        redacted_preview=redacted[:500],
        detection_uncertain=uncertain,
    )
