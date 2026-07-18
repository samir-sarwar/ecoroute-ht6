from __future__ import annotations

import random
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def uuid7() -> uuid.UUID:
    """Generate a sortable RFC 9562 UUIDv7 without depending on Python 3.14."""
    timestamp_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand_a = random.SystemRandom().getrandbits(12)
    rand_b = random.SystemRandom().getrandbits(62)
    value = timestamp_ms << 80
    value |= 0x7 << 76
    value |= rand_a << 64
    value |= 0b10 << 62
    value |= rand_b
    return uuid.UUID(int=value)
