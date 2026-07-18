"""Unit tests for the tool-tailored filler resolver (T015, US3 / FR-006 / FR-009).

Proves every known tool name maps to its own action-specific phrase and any unknown/None
name falls back to a non-empty generic phrase (silence during a lookup is the bug we fix).
"""

from __future__ import annotations

import pytest

from app.services.media.fillers import filler_for


@pytest.mark.parametrize(
    "tool_name",
    ["check_availability", "book_room", "cancel_booking", "list_bookings"],
)
def test_known_tools_get_distinct_nonempty_phrases(tool_name: str) -> None:
    phrase = filler_for(tool_name)
    assert phrase
    # The phrase is tool-specific, not the generic fallback.
    assert phrase != filler_for("something_unknown")


def test_known_tools_are_all_distinct() -> None:
    names = ["check_availability", "book_room", "cancel_booking", "list_bookings"]
    phrases = [filler_for(n) for n in names]
    assert len(set(phrases)) == len(names)


@pytest.mark.parametrize("tool_name", [None, "", "nonexistent_tool"])
def test_unknown_or_missing_tool_falls_back_to_nonempty_generic(tool_name) -> None:
    assert filler_for(tool_name)
    assert filler_for(tool_name) == filler_for("another_unknown")
