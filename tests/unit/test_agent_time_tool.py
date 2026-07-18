"""Tests for the agent current date/time helper tool."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agent import tools


def test_format_current_datetime_uses_business_timezone() -> None:
    text = tools._format_current_datetime(
        datetime(2026, 7, 9, 6, 15, 30, tzinfo=timezone.utc),
        "Asia/Karachi",
    )

    assert "2026-07-09 11:15:30 PKT (Asia/Karachi)" in text
    assert "Today is Thursday, July 09, 2026" in text


def test_current_datetime_tool_is_registered_first() -> None:
    names = [tool.name for tool in tools.TOOLS]

    assert names[0] == "get_current_datetime"
    assert "check_availability" in names
