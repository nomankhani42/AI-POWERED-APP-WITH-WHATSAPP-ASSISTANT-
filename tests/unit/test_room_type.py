"""Canonical room-type enforcement (007 US3, T018).

Write-time normalization/rejection is pure pydantic validation (no DB); the live-catalog
reads are pinned in ``tests/test_tools.py`` against MongoDB.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.db import bookings as repo
from app.db.documents import Room, RoomType

CANONICAL = ["single", "twin", "double", "deluxe", "accessible", "family", "executive", "suite"]


def test_enum_matches_canonical_catalog_in_order() -> None:
    assert [t.value for t in RoomType] == CANONICAL


def test_casing_and_spacing_variants_normalize() -> None:
    room = Room(name="Room 998", room_type=" Deluxe ", capacity=2)
    assert room.room_type is RoomType.deluxe

    room = Room(name="Room 997", room_type="SUITE", capacity=4)
    assert room.room_type is RoomType.suite


def test_unknown_type_rejected_at_write_time() -> None:
    with pytest.raises(ValidationError):
        Room(name="Room 999", room_type="penthouse", capacity=2)


def test_normalize_room_type_maps_variants_and_names_valid_types() -> None:
    assert repo.normalize_room_type(" Deluxe ") is RoomType.deluxe

    with pytest.raises(repo.BookingError) as excinfo:
        repo.normalize_room_type("penthouse")
    message = str(excinfo.value)
    # FR-008: the tool relays this so the agent can re-offer the valid options.
    assert "penthouse" in message
    for value in CANONICAL:
        assert value in message
