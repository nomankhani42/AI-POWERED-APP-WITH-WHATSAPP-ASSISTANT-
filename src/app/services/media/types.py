"""Shared transient types that flow through the voice pipeline.

These are in-memory values passed between the STT service, the agent turn, and the TTS
service — they are NOT persisted (only ``Call``/``CallEvent`` are durable; see data-model.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(slots=True)
class AgentStreamEvent:
    """One event from a streaming agent turn (feature 005, FR-006).

    A tagged union over ``kind`` so the media session can react to tool calls distinctly
    from reply text (see ``agent/service.run_turn_events`` and
    ``specs/005-fix-voice-call-flow/contracts/agent-turn-events.md``):

    - ``text_delta`` — an incremental reply chunk; ``text`` is set.
    - ``tool_call``  — a tool is about to run (trigger the tailored filler); ``tool_name`` set.
    - ``tool_output`` — the tool returned; ``tool_name`` and ``ok`` are set.

    Within one turn the SDK emits every ``tool_call``/``tool_output`` pair before the reply
    ``text_delta``s, so the session can speak a filler before the answer.
    """

    kind: Literal["text_delta", "tool_call", "tool_output"]
    text: str | None = None
    tool_name: str | None = None
    ok: bool | None = None


@dataclass(slots=True)
class TranscriptSegment:
    """A piece of recognized caller speech emitted by the STT service.

    ``is_final`` marks the end of an utterance (caller finished speaking), which is what
    triggers a single agent turn.
    """

    call_id: str
    text: str
    is_final: bool
    ts: float
    confidence: float | None = None


@dataclass(slots=True)
class SpeechChunk:
    """A chunk of synthesized audio emitted by the TTS service, forwarded to the caller."""

    call_id: str
    audio: bytes
    sample_rate: int


@dataclass(slots=True)
class ConversationTurn:
    """One exchange in a call's conversation loop, logged for observability (FR-023).

    Turn ``0`` is the automatic welcome (empty ``transcript``, ``reply`` = welcome message);
    each later turn pairs the caller's transcript with the agent's spoken reply. Like the
    other types here it is transient — written to the log, never persisted.
    """

    call_id: str
    turn: int
    transcript: str
    reply: str
    started_at: float
    ended_at: float
