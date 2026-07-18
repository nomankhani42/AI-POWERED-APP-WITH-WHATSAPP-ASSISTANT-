"""Inbound WhatsApp message idempotency (007 FR-005a).

Two dedupe layers, mirroring ``db/calls.py``: a fast Redis ``SET NX`` marker for the hot
path, backed by the durable unique ``wamid`` index on ``InboundMessage`` as a backstop.
Meta redelivers webhook events, so a message is processed only by the caller that both
sets the Redis key AND inserts the document.
"""

from __future__ import annotations

from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.documents import InboundMessage

_redis_client: Redis | None = None

# Meta's retry window is hours, not days; a day of fast-path coverage is plenty and the
# Mongo unique index catches anything older.
_DEFAULT_TTL_SECONDS = 24 * 3600


def _redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(get_settings().redis_url)
    return _redis_client


async def is_duplicate(wamid: str, *, ttl_seconds: int = _DEFAULT_TTL_SECONDS) -> bool:
    """Fast-path dedupe: True if this wamid was already marked seen in Redis."""

    was_set = await _redis().set(f"wa:msg:{wamid}", "1", nx=True, ex=ttl_seconds)
    return was_set is None


async def record(*, wamid: str, sender: str, message_type: str) -> InboundMessage | None:
    """Insert the durable ``InboundMessage``. Returns ``None`` (no error) on a duplicate."""

    message = InboundMessage(wamid=wamid, sender=sender, message_type=message_type)
    try:
        await message.insert()
    except DuplicateKeyError:
        return None
    return message
