"""Upstash Redis–backed session memory for OpenAI Agents SDK.

Implements ``SessionABC`` so it can be passed directly to
``Runner.run(session=...)``.  Conversation items are stored as a
JSON list under ``session:{session_id}`` with a configurable TTL
(default 1 hour) for automatic short-term memory expiry.

Environment variables (loaded via ``config.py``):
    UPSTASH_REDIS_REST_URL   – REST endpoint for your Upstash Redis DB.
    UPSTASH_REDIS_REST_TOKEN – Auth token for the Upstash REST API.
"""

from __future__ import annotations

import json
from typing import List

from agents.memory.session import SessionABC
from agents.items import TResponseInputItem
from upstash_redis.asyncio import Redis

# ---------------------------------------------------------------------------
# Default TTL for session keys (seconds).  The session auto-expires
# after this much *idle* time (each write resets the timer).
# ---------------------------------------------------------------------------
DEFAULT_SESSION_TTL = 3600  # 1 hour


class UpstashSession(SessionABC):
    """Upstash Redis session storage for the OpenAI Agents SDK.

    Each WhatsApp conversation gets its own Redis key
    (``session:<session_id>``) holding a JSON-serialised list of
    message items.  A TTL is applied on every write so idle
    conversations are automatically cleaned up.
    """

    def __init__(
        self,
        session_id: str,
        *,
        redis: Redis | None = None,
        ttl: int = DEFAULT_SESSION_TTL,
        key_prefix: str = "session",
    ) -> None:
        self.session_id = session_id
        self._redis = redis or Redis.from_env()
        self._ttl = ttl
        self._key = f"{key_prefix}:{session_id}"

    # -- helpers -----------------------------------------------------------

    async def _load(self) -> list:
        """Load the raw item list from Redis."""
        data = await self._redis.get(self._key)
        if data is None:
            return []
        if isinstance(data, str):
            return json.loads(data)
        if isinstance(data, list):
            return data
        return []

    async def _save(self, items: list) -> None:
        """Persist items and refresh the TTL."""
        await self._redis.set(self._key, json.dumps(items), ex=self._ttl)

    # -- SessionABC interface ----------------------------------------------

    async def get_items(self, limit: int | None = None) -> List[TResponseInputItem]:
        """Retrieve conversation history for this session."""
        items = await self._load()
        if limit is not None:
            items = items[-limit:]
        return items

    async def add_items(self, items: List[TResponseInputItem]) -> None:
        """Append new items and refresh TTL."""
        existing = await self._load()
        existing.extend(items)
        await self._save(existing)

    async def pop_item(self) -> TResponseInputItem | None:
        """Remove and return the most recent item."""
        items = await self._load()
        if not items:
            return None
        item = items.pop()
        await self._save(items)
        return item

    async def clear_session(self) -> None:
        """Delete all items for this session."""
        await self._redis.delete(self._key)


# ---------------------------------------------------------------------------
# Convenience factory
# ---------------------------------------------------------------------------

_redis_client: Redis | None = None


def _get_redis() -> Redis:
    """Lazily create a shared async Upstash Redis client."""
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_env()
    return _redis_client


def get_session(session_id: str, ttl: int = DEFAULT_SESSION_TTL) -> UpstashSession:
    """Return an ``UpstashSession`` backed by the shared Redis client.

    Args:
        session_id: Unique conversation key (e.g. WhatsApp phone number).
        ttl: Time-to-live in seconds for idle sessions (default 1 h).
    """
    return UpstashSession(session_id, redis=_get_redis(), ttl=ttl)
