"""Tool-tailored spoken fillers (feature 005, US3 / FR-006 / FR-009).

Single job: turn the tool being called into a short, natural filler phrase the agent speaks
the instant a tool call is detected, so the caller is never left in silence while a lookup or
booking runs. See ``specs/005-fix-voice-call-flow/contracts/filler-phrases.md``.

Pure helper: reads the configurable phrases from settings and returns a **non-empty** string
for every input (silence during a lookup is the defect being fixed). Kept as an in-place
helper module rather than a package.
"""

from __future__ import annotations

from app.core.config import get_settings


def filler_for(tool_name: str | None) -> str:
    """Return the spoken filler tailored to ``tool_name``.

    Known booking tools map to an action-specific phrase; any unknown or missing name falls
    back to the generic filler. Never returns an empty string.
    """

    settings = get_settings()
    mapping = {
        "check_availability": settings.filler_check_availability,
        "book_room": settings.filler_book_room,
        "cancel_booking": settings.filler_cancel_booking,
        "list_bookings": settings.filler_list_bookings,
    }
    phrase = mapping.get(tool_name or "", "") or settings.filler_generic
    # Defensive: never let a blank override produce silence during a lookup.
    return phrase or "Let me check that for you…"
