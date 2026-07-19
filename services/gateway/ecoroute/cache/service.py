from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, cast

from redis.asyncio import Redis
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ecoroute.api.schemas import NormalizedRequestFeatures
from ecoroute.cache.embeddings import get_local_embedder
from ecoroute.config import Settings
from ecoroute.db.models import CacheEntry


def normalize_semantic_text(value: str) -> str:
    """Conservatively canonicalize known policy-question paraphrases."""
    normalized = unicodedata.normalize("NFKC", value).lower()
    normalized = re.sub(r"[^\w\s-]", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    has_return = bool(re.search(r"\b(return|returns|send back|sending back)\b", normalized))
    has_unused_item = bool(
        re.search(r"\bunused\b", normalized)
        and re.search(r"\b(item|items|product|products|something)\b", normalized)
    )
    asks_window = bool(re.search(r"\breturn window\b|\bhow many days\b|\bhow long\b", normalized))
    if has_return and has_unused_item and asks_window:
        return "return window unused item"
    return normalized


class CacheService:
    def __init__(self, redis: Redis, settings: Settings) -> None:
        self.redis = redis
        self.embedder = get_local_embedder(
            settings.embedding_model, use_sentence_transformers=settings.use_sentence_transformers
        )

    @staticmethod
    def exact_key(workspace_id: uuid.UUID, fingerprint: str) -> str:
        return f"ecoroute:exact:{workspace_id}:{fingerprint}"

    async def exact_get(self, workspace_id: uuid.UUID, fingerprint: str) -> dict[str, Any] | None:
        value = await self.redis.get(self.exact_key(workspace_id, fingerprint))
        return json.loads(value) if value else None

    async def exact_set(
        self, workspace_id: uuid.UUID, fingerprint: str, value: dict[str, Any], ttl: int
    ) -> None:
        await self.redis.set(
            self.exact_key(workspace_id, fingerprint),
            json.dumps(value, separators=(",", ":")),
            ex=ttl,
        )

    async def semantic_find(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        logical_model_id: uuid.UUID,
        features: NormalizedRequestFeatures,
        namespace_version: int,
        threshold: float,
    ) -> CacheEntry | None:
        now = datetime.now(timezone.utc)
        query = self.embedder.encode(normalize_semantic_text(features.normalized_text))
        distance = CacheEntry.embedding.cosine_distance(query)
        filters = [
            CacheEntry.workspace_id == workspace_id,
            CacheEntry.logical_model_id == logical_model_id,
            CacheEntry.namespace_version == namespace_version,
            CacheEntry.system_prompt_hash == features.system_prompt_hash,
            CacheEntry.language == features.requested_language,
            CacheEntry.expires_at > now,
            CacheEntry.invalidated_at.is_(None),
            CacheEntry.embedding.is_not(None),
            CacheEntry.quality_verdict["task_type"].astext.in_(["policy_qa"]),
        ]
        filters.append(
            CacheEntry.tool_schema_hash == features.tool_schema_hash
            if features.tool_schema_hash is not None
            else CacheEntry.tool_schema_hash.is_(None)
        )
        filters.append(
            CacheEntry.response_format_hash == features.response_format_hash
            if features.response_format_hash is not None
            else CacheEntry.response_format_hash.is_(None)
        )
        statement = (
            select(CacheEntry, distance.label("distance"))
            .where(
                *filters,
            )
            .order_by(distance)
            .limit(3)
        )
        rows = list((await session.execute(statement)).all())
        if not rows:
            return None
        ranked = [(1 - float(row[1]), row[0]) for row in rows]
        if not ranked or ranked[0][0] < threshold:
            return None
        if len(ranked) > 1 and ranked[0][0] - ranked[1][0] < 0.02:
            return None
        entry = cast(CacheEntry, ranked[0][1])
        entry.hit_count += 1
        entry.last_hit_at = now
        return entry

    async def store_entry(
        self,
        session: AsyncSession,
        *,
        workspace_id: uuid.UUID,
        logical_model_id: uuid.UUID,
        source_request_id: uuid.UUID,
        source_endpoint_id: uuid.UUID,
        fingerprint: str,
        namespace_version: int,
        features: NormalizedRequestFeatures,
        completion: dict[str, Any],
        quality_verdict: dict[str, Any],
        baseline_energy_kwh: float,
        baseline_cost_usd: Decimal,
        ttl_seconds: int,
        task_type: str,
    ) -> CacheEntry:
        now = datetime.now(timezone.utc)
        lock_key = int.from_bytes(
            hashlib.sha256(f"{workspace_id}:{namespace_version}:{fingerprint}".encode()).digest()[
                :8
            ],
            "big",
            signed=True,
        )
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": lock_key}
        )
        existing = await session.scalar(
            select(CacheEntry).where(
                CacheEntry.workspace_id == workspace_id,
                CacheEntry.namespace_version == namespace_version,
                CacheEntry.exact_fingerprint == fingerprint,
                CacheEntry.invalidated_at.is_(None),
            )
        )
        if existing is not None:
            if existing.expires_at > now:
                await self.exact_set(
                    workspace_id,
                    fingerprint,
                    {
                        "completion": existing.completion,
                        "source_request_id": str(existing.source_request_id),
                        "source_endpoint_id": str(existing.source_endpoint_id),
                        "baseline_energy_kwh": existing.baseline_energy_kwh,
                        "baseline_cost_usd": str(existing.baseline_cost_usd),
                    },
                    max(60, int((existing.expires_at - now).total_seconds())),
                )
                return existing
            existing.invalidated_at = now
            await session.flush()
        entry = CacheEntry(
            workspace_id=workspace_id,
            logical_model_id=logical_model_id,
            exact_fingerprint=fingerprint,
            namespace_version=namespace_version,
            system_prompt_hash=features.system_prompt_hash,
            tool_schema_hash=features.tool_schema_hash,
            response_format_hash=features.response_format_hash,
            language=features.requested_language,
            normalized_semantic_text=normalize_semantic_text(features.normalized_text),
            embedding=self.embedder.encode(normalize_semantic_text(features.normalized_text))
            if task_type == "policy_qa"
            else None,
            completion=completion,
            source_request_id=source_request_id,
            source_endpoint_id=source_endpoint_id,
            quality_verdict=quality_verdict,
            baseline_energy_kwh=baseline_energy_kwh,
            baseline_cost_usd=baseline_cost_usd,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        session.add(entry)
        await self.exact_set(
            workspace_id,
            fingerprint,
            {
                "completion": completion,
                "source_request_id": str(source_request_id),
                "source_endpoint_id": str(source_endpoint_id),
                "baseline_energy_kwh": baseline_energy_kwh,
                "baseline_cost_usd": str(baseline_cost_usd),
            },
            ttl_seconds,
        )
        return entry


def freshen_cached_completion(completion: dict[str, Any]) -> dict[str, Any]:
    value = json.loads(json.dumps(completion))
    value["id"] = f"chatcmpl-{uuid.uuid4().hex}"
    value["created"] = int(datetime.now(timezone.utc).timestamp())
    return cast(dict[str, Any], value)
