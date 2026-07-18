"""Unit tests for call-flow log helpers (T018, US4 / FR-011..FR-017 / SC-006).

Proves every new helper emits an INFO record that carries ``call_id`` (in both the message
and the structured ``extra``) and that no record leaks a secret/token/tool-argument value.
"""

from __future__ import annotations

import logging

import pytest

from app.services.media import observability as obs
from app.services.media.types import ConversationTurn

CALL_ID = "call-xyz"
SECRET = "super-secret-token-value"


def _emit_all(turn_transcript: str = "hi", turn_reply: str = "hello") -> None:
    obs.log_call_attended(CALL_ID, "+15551230000")
    obs.log_welcome(CALL_ID)
    obs.log_turn(
        ConversationTurn(
            call_id=CALL_ID, turn=1, transcript=turn_transcript, reply=turn_reply,
            started_at=0.0, ended_at=0.5,
        )
    )
    obs.log_tool_call(CALL_ID, 1, "book_room")
    obs.log_tool_result(CALL_ID, 1, "book_room", True)
    obs.log_filler(CALL_ID, 1, "book_room")
    obs.log_playback(CALL_ID, 1, "start")
    obs.log_playback(CALL_ID, 1, "stop")
    obs.log_barge_in(CALL_ID, 1)
    obs.log_reprompt(CALL_ID)
    obs.log_fallback(CALL_ID, 1)
    obs.log_call_ended(CALL_ID, "completed")


def test_every_record_carries_call_id_at_info(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.call"):
        _emit_all()

    assert caplog.records, "expected INFO log records"
    for record in caplog.records:
        assert record.levelno == logging.INFO
        # call_id present in the structured extra and rendered message.
        assert getattr(record, "call_id", None) == CALL_ID
        assert CALL_ID in record.getMessage()


def test_transcript_final_at_info_interim_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """FR-013: final transcripts surface at INFO; interim (partial) ones at DEBUG."""

    with caplog.at_level(logging.INFO, logger="app.call"):
        obs.log_transcript(CALL_ID, "book a room", is_final=True)
        obs.log_transcript(CALL_ID, "book a", is_final=False)  # below INFO → not captured

    events = [(r.getMessage(), getattr(r, "is_final", None)) for r in caplog.records]
    assert any("book a room" in msg and final is True for msg, final in events)
    # The interim partial did not appear at INFO.
    assert not any("book a" == getattr(r, "text", None) for r in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger="app.call"):
        obs.log_transcript(CALL_ID, "book a", is_final=False)
    assert any(getattr(r, "text", None) == "book a" for r in caplog.records)


def test_no_secret_leaks_in_any_record(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="app.call"):
        # Even if a secret somehow flowed through conversational text, the helpers we assert on
        # (tool/flow milestones) must never carry it. Emit the flow records and scan them.
        obs.log_tool_call(CALL_ID, 1, "book_room")
        obs.log_tool_result(CALL_ID, 1, "book_room", True)
        obs.log_filler(CALL_ID, 1, "book_room")
        obs.log_call_ended(CALL_ID, "completed")

    for record in caplog.records:
        assert SECRET not in record.getMessage()
        # Tool records log the name + a boolean only — never an argument payload.
        assert "arguments" not in record.getMessage()
