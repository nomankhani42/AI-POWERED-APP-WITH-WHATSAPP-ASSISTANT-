"""Call record persistence + event idempotency (T009).

Two dedupe layers (data-model.md): a fast Redis ``SET NX`` marker for the hot path, backed by
the durable unique ``event_id`` index on ``CallEvent`` as a backstop. ``upsert_call`` applies
the monotonic state-transition rule — a call already ``ended``/``failed`` never regresses.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pymongo.errors import DuplicateKeyError
from redis.asyncio import Redis

from app.core.config import get_settings
from app.db.documents import Call, CallEvent, CallStatus

_TERMINAL = {CallStatus.ended, CallStatus.failed}

_redis_client: Redis | None = None


def _utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


def _redis() -> Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = Redis.from_url(get_settings().redis_url)
    return _redis_client


async def is_duplicate(event_id: str, *, ttl_seconds: int = 300) -> bool:
    """Fast-path dedupe: True if this event_id was already marked seen in Redis."""

    key = f"call:event:{event_id}"
    was_set = await _redis().set(key, "1", nx=True, ex=ttl_seconds)
    return was_set is None


async def record_event(
    *, event_id: str, call_id: str, event_type: str, payload: dict
) -> CallEvent | None:
    """Insert the durable ``CallEvent``. Returns ``None`` (no error) on a duplicate insert."""

    event = CallEvent(event_id=event_id, call_id=call_id, event_type=event_type, payload=payload)
    try:
        await event.insert()
    except DuplicateKeyError:
        return None
    return event


def apply_transition(
    call: Call, *, status: CallStatus | None, end_reason: str | None = None
) -> None:
    """Mutate ``call`` per the monotonic state-transition rule (data-model.md).

    A call already ``ended``/``failed`` never regresses to an earlier state. Entering
    ``connected`` records ``connected_at`` once; entering a terminal state records
    ``ended_at``/``end_reason``.
    """

    if call.status in _TERMINAL or status is None:
        return

    call.status = status
    if status == CallStatus.connected and call.connected_at is None:
        call.connected_at = _utcnow()
    if status in _TERMINAL:
        call.ended_at = _utcnow()
        call.end_reason = end_reason


async def upsert_call(
    *,
    call_id: str,
    status: CallStatus | None = None,
    wa_call_from: str | None = None,
    display_phone_number: str | None = None,
    conversation_id: str | None = None,
    end_reason: str | None = None,
) -> Call:
    """Create-or-update the ``Call`` record, applying the monotonic state transition.

    An out-of-order ``terminate`` for a call never seen before creates the record already in
    the target terminal state rather than orphaning the event (data-model.md edge case).
    """

    call = await Call.find_one({"call_id": call_id})
    if call is None:
        call = Call(
            call_id=call_id,
            wa_call_from=wa_call_from or "",
            display_phone_number=display_phone_number or "",
            conversation_id=conversation_id or wa_call_from or call_id,
        )
        apply_transition(call, status=status, end_reason=end_reason)
        await call.insert()
        return call

    if wa_call_from:
        call.wa_call_from = wa_call_from
    if display_phone_number:
        call.display_phone_number = display_phone_number
    apply_transition(call, status=status, end_reason=end_reason)
    await call.save()
    return call
