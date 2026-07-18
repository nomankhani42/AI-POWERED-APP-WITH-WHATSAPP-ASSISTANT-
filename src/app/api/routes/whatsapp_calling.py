"""WhatsApp Business Calling webhook — verification + call lifecycle events (T011/US1).

See contracts/whatsapp-calling-webhook.md. The POST handler ALWAYS returns 200 to Meta;
every exception is caught internally so one bad event never fails the batch or triggers
Meta's retry floods (FR-004, skill hard-rule #1). Live media (T019/T017/T018) is started
as a background task from the ``connect`` branch below so the 200 response is never
blocked on SDP negotiation or the STT/agent/TTS pipeline spinning up.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request, Response
from fastapi.responses import PlainTextResponse

from app.core.config import get_settings
from app.db import calls
from app.db.documents import CallStatus
from app.services import meta_calling, whatsapp_chat
from app.services.media import session as media_session

logger = logging.getLogger(__name__)

router = APIRouter(tags=["whatsapp-calling"])

# Background media-start tasks must be referenced somewhere or asyncio may garbage-collect
# (and silently cancel) them mid-flight — see asyncio docs "Important: Save a reference".
_background_tasks: set[asyncio.Task] = set()


def _fire_and_forget(coro: object) -> None:
    task = asyncio.create_task(coro)  # type: ignore[arg-type]
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


# Lifecycle event -> Call.status. Anything not listed here (e.g. "status") is recorded but
# does not change the Call's status (FR-005).
_EVENT_STATUS: dict[str, CallStatus] = {
    "connect": CallStatus.connecting,
    "terminate": CallStatus.ended,
}


@router.get("/whatsapp/webhook")
async def verify_webhook(request: Request) -> Response:
    """Meta's one-time verification handshake (FR-002)."""

    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge", "")

    settings = get_settings()
    if mode == "subscribe" and token == settings.whatsapp_verify_token:
        return PlainTextResponse(challenge, status_code=200)

    # Wrong/missing token: reject without leaking why (edge case: verification token mismatch).
    return Response(status_code=403)


@router.post("/whatsapp/webhook")
async def receive_call_event(request: Request) -> dict:
    """Call lifecycle events. Always acknowledges 200, even on invalid/malformed input."""

    try:
        raw_body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")

        settings = get_settings()
        if settings.whatsapp_skip_signature:
            logger.warning(
                "whatsapp calling webhook: SIGNATURE VERIFICATION DISABLED "
                "(WHATSAPP_SKIP_SIGNATURE=true) — local debug only, do not use in production"
            )
        elif not meta_calling.verify_signature(raw_body, signature):
            logger.warning("whatsapp calling webhook: rejected event with invalid signature")
            return {"status": "received"}

        payload = await request.json()
        await _process_payload(payload)
    except Exception:  # noqa: BLE001 - never fail this endpoint to Meta (FR-004)
        logger.exception("whatsapp calling webhook: unhandled error processing event")

    return {"status": "received"}


async def _process_payload(payload: dict) -> None:
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            metadata = value.get("metadata", {})
            display_phone_number = metadata.get("display_phone_number", "")
            for call_event in value.get("calls", []):
                await _process_call_event(call_event, display_phone_number)
            # Inbound chat messages (007 FR-005a): each runs as a background task so the
            # 200 ack never waits on an agent turn; the task dedupes by wamid and exits
            # early on redelivery. Delivery receipts in value["statuses"] are ignored.
            for message in value.get("messages", []):
                _fire_and_forget(whatsapp_chat.process_message(message, display_phone_number))


async def _process_call_event(call_event: dict, display_phone_number: str) -> None:
    call_id = call_event.get("id")
    if not call_id:
        return

    event_type = call_event.get("event", "status")
    wa_call_from = call_event.get("from", "")
    timestamp = call_event.get("timestamp", "")
    # Meta's calling webhook does not carry a distinct delivery id, so one is synthesized from
    # the immutable parts of the event for idempotency (FR-006).
    event_id = f"{call_id}:{event_type}:{timestamp}"

    try:
        if await calls.is_duplicate(event_id):
            return

        recorded = await calls.record_event(
            event_id=event_id,
            call_id=call_id,
            event_type=event_type,
            payload=call_event,
        )
        if recorded is None:
            return  # durable unique-index backstop caught a duplicate Redis missed

        status = _EVENT_STATUS.get(event_type)
        end_reason = "caller_hangup" if event_type == "terminate" else None
        await calls.upsert_call(
            call_id=call_id,
            wa_call_from=wa_call_from,
            display_phone_number=display_phone_number,
            status=status,
            end_reason=end_reason,
        )

        if event_type == "connect":
            call_session = call_event.get("session", {})
            sdp_offer = call_session.get("sdp")
            if sdp_offer:
                # Scheduled (not awaited): SDP negotiation + spinning up the STT/agent/TTS
                # loop must never block this webhook's 200 ack (FR-004).
                _fire_and_forget(_start_media(call_id, wa_call_from, sdp_offer))
        elif event_type == "terminate":
            _fire_and_forget(media_session.stop_session(call_id))
    except Exception:
        logger.exception(
            "whatsapp calling webhook: failed processing call_id=%s event=%s",
            call_id,
            event_type,
        )


async def _start_media(call_id: str, caller: str, sdp_offer: str) -> None:
    """Negotiate, accept, persist, then start the call loop in lifecycle order."""

    try:
        # A second connect notification with a different timestamp must not replace a live
        # peer connection or replay the welcome.
        if media_session.has_session(call_id):
            logger.info("whatsapp calling webhook: media already active call_id=%s", call_id)
            return

        answer_sdp = await media_session.prepare_session(call_id, caller, sdp_offer)
        # Debug: show raw SDP with repr() so \r\n is visible, and confirm the audio-section
        # attributes WhatsApp's validator requires are present before we POST the accept.
        logger.debug(
            "accept() answer SDP for call_id=%s: candidate=%d ice-ufrag=%d ice-pwd=%d "
            "fingerprint=%d raw=%r",
            call_id,
            answer_sdp.count("a=candidate"),
            answer_sdp.count("a=ice-ufrag"),
            answer_sdp.count("a=ice-pwd"),
            answer_sdp.count("a=fingerprint"),
            answer_sdp,
        )
        # Pre-accept (recommended by Meta): lets ICE/DTLS establish while the call is still
        # ringing, so media is already flowing when accept lands and the welcome isn't
        # clipped by transport setup. Best-effort — accept alone still connects the call.
        try:
            await meta_calling.pre_accept(call_id, answer_sdp)
        except Exception:
            logger.warning(
                "whatsapp calling webhook: pre_accept failed for call_id=%s; "
                "continuing with accept",
                call_id,
                exc_info=True,
            )
        await meta_calling.accept(call_id, answer_sdp)

        # A terminate webhook may have removed the prepared session while accept was in
        # flight. In that case do not resurrect the conversation loop.
        if not media_session.has_session(call_id) or not await media_session.wait_session_ready(
            call_id
        ):
            return

        try:
            await calls.upsert_call(call_id=call_id, status=CallStatus.connected)
        except Exception:
            # Persistence failure should not turn an already accepted call into dead air.
            logger.exception(
                "whatsapp calling webhook: failed to persist connected call_id=%s", call_id
            )

        media_session.activate_session(call_id)
    except Exception:
        logger.exception(
            "whatsapp calling webhook: failed to start media session for call_id=%s", call_id
        )
        await media_session.stop_session(call_id)
        try:
            await calls.upsert_call(
                call_id=call_id,
                status=CallStatus.failed,
                end_reason="media_start_failure",
            )
        except Exception:
            logger.exception(
                "whatsapp calling webhook: failed to persist media failure call_id=%s",
                call_id,
            )
