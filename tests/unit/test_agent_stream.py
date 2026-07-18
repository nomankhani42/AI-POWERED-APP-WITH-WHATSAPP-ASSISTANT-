"""Unit tests for the streaming agent turn (T018, US3).

Mocks ``Runner.run_streamed`` (and the agent/session/context builders) at the module
boundary so no OpenAI or Redis access happens. Proves ``run_turn_stream`` yields the reply
as ordered text deltas whose join is the full reply, ignoring non-text events (FR-007/008).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openai.types.responses import ResponseTextDeltaEvent

from app.agent import service


class _Raw:
    """A ``raw_response_event`` carrying a text-delta payload."""

    type = "raw_response_event"

    def __init__(self, delta: str) -> None:
        self.data = ResponseTextDeltaEvent.model_construct(delta=delta)


class _Other:
    """A non-text event (e.g. tool-call args / run-item) the text stream must ignore."""

    type = "run_item_stream_event"
    data = SimpleNamespace(delta="SHOULD-NOT-APPEAR")


class _FakeStream:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def stream_events(self):
        for event in self._events:
            await asyncio.sleep(0)  # yield control, like a real stream
            yield event


def _patch(monkeypatch, events: list[object]) -> None:
    monkeypatch.setattr(service, "build_agent", lambda: object())
    monkeypatch.setattr(service, "RedisSession", lambda conv_id: object())
    monkeypatch.setattr(service, "RunContext", lambda **kwargs: object())
    monkeypatch.setattr(
        service,
        "Runner",
        SimpleNamespace(run_streamed=lambda *a, **k: _FakeStream(events)),
    )


async def test_run_turn_stream_yields_ordered_deltas(monkeypatch) -> None:
    events = [_Raw("Hel"), _Other(), _Raw("lo"), _Raw(" there")]
    _patch(monkeypatch, events)

    deltas = [d async for d in service.run_turn_stream("hi", phone_number="+15550001111")]

    assert deltas == ["Hel", "lo", " there"]
    assert "".join(deltas) == "Hello there"


async def test_run_turn_stream_skips_empty_deltas(monkeypatch) -> None:
    events = [_Raw("A"), _Raw(""), _Raw("B")]
    _patch(monkeypatch, events)

    deltas = [d async for d in service.run_turn_stream("hi", phone_number="+15550002222")]

    assert deltas == ["A", "B"]


class _RunItem:
    """A ``run_item_stream_event`` carrying a tool-call or tool-output item (feature 005)."""

    type = "run_item_stream_event"

    def __init__(self, item_type: str, name: str) -> None:
        self.item = SimpleNamespace(type=item_type, raw_item=SimpleNamespace(name=name))


async def test_run_turn_events_maps_tool_calls_and_text(monkeypatch) -> None:
    """FR-006: run_turn_events surfaces tool_call/tool_output events, in order, plus text."""

    events = [
        _RunItem("tool_call_item", "check_availability"),
        _RunItem("tool_call_output_item", "check_availability"),
        _Raw("Room "),
        _Raw("5 is free"),
    ]
    _patch(monkeypatch, events)

    out = [e async for e in service.run_turn_events("hi", phone_number="+15550003333")]

    kinds = [(e.kind, e.tool_name, e.text) for e in out]
    assert kinds == [
        ("tool_call", "check_availability", None),
        ("tool_output", "check_availability", None),
        ("text_delta", None, "Room "),
        ("text_delta", None, "5 is free"),
    ]
    # The tool call is emitted before the reply text (lets the session speak a filler first).
    assert kinds[0][0] == "tool_call"
    assert kinds[-1][0] == "text_delta"
    assert out[1].ok is True
