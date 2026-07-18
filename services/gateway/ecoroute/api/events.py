from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from redis.asyncio import Redis

from ecoroute.config import Settings


async def publish_event(
    redis: Redis,
    settings: Settings,
    workspace_id: uuid.UUID,
    event_type: str,
    data: dict[str, Any],
) -> str:
    stream = f"ecoroute:events:{workspace_id}"
    payload = {
        "type": event_type,
        "occurredAt": datetime.now(timezone.utc).isoformat(),
        "workspaceId": str(workspace_id),
        "data": json.dumps(data, separators=(",", ":"), default=str),
    }
    result = await redis.xadd(
        stream,
        payload,  # type: ignore[arg-type]
        maxlen=settings.event_stream_maxlen,
        approximate=True,
    )
    return result.decode() if isinstance(result, bytes) else result
