"""Structured logging helpers for call observability (US4).

Single job: emit the two observability records the conversation loop produces —
``call_attended`` when a call connects (FR-015) and one ``call_turn`` per exchange
(FR-023). Kept out of ``session.py`` so the orchestrator stays focused on the loop.

Only the caller's number and ``call_id`` are logged — never tokens or secrets
(constitution Principle V).
"""

from __future__ import annotations

import logging

from app.services.media.types import ConversationTurn

logger = logging.getLogger("app.call")


def log_call_attended(call_id: str, wa_call_from: str) -> None:
    """Record that a call was attended, with the caller's number and call id (FR-015)."""

    logger.info(
        "call_attended call_id=%s from=%s",
        call_id,
        wa_call_from,
        extra={"event": "call_attended", "call_id": call_id, "from": wa_call_from},
    )


def log_turn(turn: ConversationTurn) -> None:
    """Record one conversation turn (welcome = turn 0, then each transcript/reply) (FR-023)."""

    logger.info(
        "call_turn call_id=%s turn=%d transcript=%r reply=%r",
        turn.call_id,
        turn.turn,
        turn.transcript,
        turn.reply,
        extra={
            "event": "call_turn",
            "call_id": turn.call_id,
            "turn": turn.turn,
            "duration_s": round(turn.ended_at - turn.started_at, 3),
        },
    )


# --- Feature 005: full call-flow milestones (US4 / FR-011..FR-017) ---------------------
# Each helper emits an INFO record carrying ``call_id`` so an operator can reconstruct one
# call's timeline from the logs alone. Only tool *names* and booleans are logged — never tool
# argument values, tokens, or secrets (FR-017 / SC-006).


def log_transcript(
    call_id: str, text: str, is_final: bool, confidence: float | None = None
) -> None:
    """Record what STT heard from the caller, so transcription can be verified live (FR-013).

    Final (end-of-turn) transcripts log at INFO so they show in normal backend logs; interim
    (partial) transcripts log at DEBUG so live typing-out of speech is available when
    ``LOG_LEVEL=DEBUG`` without flooding INFO.
    """

    logger.log(
        logging.INFO if is_final else logging.DEBUG,
        "call_transcript call_id=%s final=%s confidence=%s text=%r",
        call_id,
        is_final,
        f"{confidence:.3f}" if confidence is not None else "unknown",
        text,
        extra={
            "event": "call_transcript",
            "call_id": call_id,
            "is_final": is_final,
            "text": text,
            "confidence": confidence,
        },
    )


def log_welcome(call_id: str) -> None:
    """Record that the welcome greeting started playing (FR-012)."""

    logger.info(
        "call_welcome call_id=%s",
        call_id,
        extra={"event": "call_welcome", "call_id": call_id},
    )


def log_tool_call(call_id: str, turn: int, tool: str | None) -> None:
    """Record that the agent is about to run a tool during ``turn`` (FR-014)."""

    logger.info(
        "call_tool_call call_id=%s turn=%d tool=%s",
        call_id,
        turn,
        tool,
        extra={"event": "call_tool_call", "call_id": call_id, "turn": turn, "tool": tool},
    )


def log_tool_result(call_id: str, turn: int, tool: str | None, ok: bool) -> None:
    """Record a tool's outcome (success/failure) — never its argument values (FR-014/FR-017)."""

    logger.info(
        "call_tool_result call_id=%s turn=%d tool=%s ok=%s",
        call_id,
        turn,
        tool,
        ok,
        extra={
            "event": "call_tool_result",
            "call_id": call_id,
            "turn": turn,
            "tool": tool,
            "ok": ok,
        },
    )


def log_filler(call_id: str, turn: int, tool: str | None) -> None:
    """Record that a tool-tailored filler phrase was spoken (FR-015)."""

    logger.info(
        "call_filler call_id=%s turn=%d tool=%s",
        call_id,
        turn,
        tool,
        extra={"event": "call_filler", "call_id": call_id, "turn": turn, "tool": tool},
    )


def log_playback(call_id: str, turn: int, state: str) -> None:
    """Record reply playback start/stop; ``state`` is 'start' or 'stop' (FR-015)."""

    logger.info(
        "call_playback call_id=%s turn=%d state=%s",
        call_id,
        turn,
        state,
        extra={"event": "call_playback", "call_id": call_id, "turn": turn, "state": state},
    )


def log_barge_in(call_id: str, turn: int) -> None:
    """Record that the caller interrupted a reply (FR-015)."""

    logger.info(
        "call_barge_in call_id=%s turn=%d",
        call_id,
        turn,
        extra={"event": "call_barge_in", "call_id": call_id, "turn": turn},
    )


def log_reprompt(call_id: str) -> None:
    """Record that a silence re-prompt was spoken (FR-015)."""

    logger.info(
        "call_reprompt call_id=%s",
        call_id,
        extra={"event": "call_reprompt", "call_id": call_id},
    )


def log_fallback(call_id: str, turn: int) -> None:
    """Record that a spoken fallback/apology was played for ``turn`` (FR-015)."""

    logger.info(
        "call_fallback call_id=%s turn=%d",
        call_id,
        turn,
        extra={"event": "call_fallback", "call_id": call_id, "turn": turn},
    )


def log_call_ended(call_id: str, reason: str) -> None:
    """Record that the call ended and why (FR-015)."""

    logger.info(
        "call_ended call_id=%s reason=%s",
        call_id,
        reason,
        extra={"event": "call_ended", "call_id": call_id, "reason": reason},
    )
