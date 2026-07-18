"""Redis-backed short-term conversation session for the Agents SDK.

Implements the SDK's `SessionABC` so `Runner.run(..., session=...)` transparently threads
conversation context. Context lives in Redis under a per-conversation key with a TTL, so it
is short-term only (constitution Principle III; spec Clarifications). Booking records are
durable and stored separately in MongoDB.
"""

from __future__ import annotations

import json

from agents.items import TResponseInputItem
from agents.memory.session import SessionABC
from redis.asyncio import Redis


class RedisSession(SessionABC):
    """Conversation history stored as a JSON list in a single Redis key with a TTL."""

    def __init__(
        self,
        session_id: str,
        *,
        redis: Redis | None = None,
        url: str | None = None,
        ttl: int | None = None,
    ) -> None:
        from app.core.config import get_settings

        settings = get_settings()
        self.session_id = session_id
        self._redis = redis or Redis.from_url(url or settings.redis_url)
        self._ttl = ttl if ttl is not None else settings.session_ttl_seconds
        self._key = f"agent:session:{session_id}"

    async def _read(self) -> list[TResponseInputItem]:
        raw = await self._redis.get(self._key)
        if not raw:
            return []
        return json.loads(raw)

    async def _write(self, items: list[TResponseInputItem]) -> None:
        await self._redis.set(self._key, json.dumps(items), ex=self._ttl)

    async def get_items(self, limit: int | None = None) -> list[TResponseInputItem]:
        items = await self._read()
        if limit is not None and limit >= 0:
            return items[-limit:]
        return items

    async def add_items(self, items: list[TResponseInputItem]) -> None:
        if not items:
            return
        current = await self._read()
        current.extend(items)
        await self._write(current)

    async def pop_item(self) -> TResponseInputItem | None:
        items = await self._read()
        if not items:
            return None
        last = items.pop()
        await self._write(items)
        return last

    async def clear_session(self) -> None:
        await self._redis.delete(self._key)
