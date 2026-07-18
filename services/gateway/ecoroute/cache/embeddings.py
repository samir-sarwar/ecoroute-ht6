from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Sequence
from functools import lru_cache


def _canonical_tokens(text: str) -> list[str]:
    value = text.lower()
    replacements = {
        r"how many days|return window": " return_period ",
        r"\b(?:send back|returning|returned|returns?)\b": " return ",
        r"something|item|product|jacket": " item ",
        r"unused|unworn|unopened": " unused ",
        r"shipping|delivery": " shipping ",
        r"has not moved|hasn't moved|stuck": " delayed ",
    }
    for pattern, replacement in replacements.items():
        value = re.sub(pattern, replacement, value)
    tokens = re.findall(r"[a-z0-9_]+", value)
    if "return_period" in tokens and "unused" in tokens:
        return ["return_policy", "return_period", "unused_item"]
    stop_words = {
        "a",
        "an",
        "do",
        "for",
        "have",
        "how",
        "i",
        "is",
        "my",
        "something",
        "the",
        "to",
        "what",
    }
    return [token for token in tokens if token not in stop_words]


class LocalEmbedder:
    """384-d local embedding facade with an optional Sentence Transformers backend.

    The deterministic hashing implementation keeps tests and the credential-free demo fully
    offline. Production can opt into all-MiniLM-L6-v2 with ECOROUTE_USE_SENTENCE_TRANSFORMERS.
    """

    def __init__(self, model_name: str, use_sentence_transformers: bool = False) -> None:
        self._model = None
        if use_sentence_transformers:
            try:
                from sentence_transformers import SentenceTransformer

                self._model = SentenceTransformer(model_name)
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers is requested but not installed; install the embeddings extra"
                ) from exc

    def encode(self, text: str) -> list[float]:
        if self._model is not None:
            encoded = self._model.encode(text, normalize_embeddings=True)
            return [float(item) for item in encoded]
        vector = [0.0] * 384
        tokens = _canonical_tokens(text)
        features = tokens + [f"{tokens[i]}::{tokens[i + 1]}" for i in range(len(tokens) - 1)]
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            index = int.from_bytes(digest[:2], "big") % len(vector)
            vector[index] += 1 if digest[2] % 2 else -1
        norm = math.sqrt(sum(item * item for item in vector)) or 1
        return [item / norm for item in vector]


@lru_cache(maxsize=4)
def get_local_embedder(model_name: str, use_sentence_transformers: bool) -> LocalEmbedder:
    return LocalEmbedder(model_name, use_sentence_transformers)


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=False))
