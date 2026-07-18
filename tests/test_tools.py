"""Booking repository logic tests (T016, T020, T023, T026).

These exercise the durable data layer against MongoDB. When no MongoDB is reachable the
whole module is skipped, so the suite still passes in environments without a database.
"""

from datetime import date, timedelta

import pytest
import pytest_asyncio

from app.db import bookings as repo
from app.db.documents import Booking, BookingStatus, Room
from app.db.mongo import close_db, init_db

D1 = date.today() + timedelta(days=10)
D2 = date.today() + timedelta(days=12)
D3 = date.today() + timedelta(days=11)  # overlaps [D1, D2)
D4 = date.today() + timedelta(days=14)

PHONE_A = "+1000000001"
PHONE_B = "+1000000002"


@pytest_asyncio.fixture
async def db():
    from pymongo import AsyncMongoClient

    from app.core.config import get_settings

    settings = get_settings()
    probe = AsyncMongoClient(settings.mongodb_uri, serverSelectionTimeoutMS=800)
    try:
        await probe.admin.command("ping")
    except Exception:  # noqa: BLE001 - any connection failure means skip
        pytest.skip("No MongoDB available")
    finally:
        await probe.close()

    await init_db()
    await Booking.find_all().delete()
    await Room.find_all().delete()
    yield
    await Booking.find_all().delete()
    await Room.find_all().delete()
    await close_db()


async def _room(name: str = "Room 12", room_type: str = "double") -> Room:
    return await Room(name=name, room_type=room_type, capacity=2).insert()


async def test_availability_excludes_overlapping(db) -> None:
    await _room()
    assert len(await repo.check_availability(D1, D2)) == 1

    await repo.create_booking("Room 12", D1, D2, PHONE_A)
    assert await repo.check_availability(D3, D4) == []  # D3 overlaps existing
    assert len(await repo.check_availability(D2, D4)) == 1  # starts at checkout → free


async def test_create_booking_and_double_booking_guard(db) -> None:
    await _room()
    booking = await repo.create_booking("Room 12", D1, D2, PHONE_A)
    assert booking.reference.startswith("BK-")
    assert booking.status == BookingStatus.active

    with pytest.raises(repo.BookingError):
        await repo.create_booking("Room 12", D3, D4, PHONE_B)  # overlaps


async def test_invalid_ranges_rejected(db) -> None:
    await _room()
    with pytest.raises(repo.BookingError):
        await repo.create_booking("Room 12", D2, D1, PHONE_A)  # checkout before checkin
    with pytest.raises(repo.BookingError):
        await repo.check_availability(date.today() - timedelta(days=1), D1)  # past


async def test_cancel_scoped_to_owner(db) -> None:
    await _room()
    booking = await repo.create_booking("Room 12", D1, D2, PHONE_A)

    assert await repo.cancel_booking(booking.reference, PHONE_B) is None  # not owner
    cancelled = await repo.cancel_booking(booking.reference, PHONE_A)
    assert cancelled is not None and cancelled.status == BookingStatus.cancelled
    # room is free again after cancellation
    assert len(await repo.check_availability(D1, D2)) == 1


async def test_cancel_transition_is_exactly_once(db) -> None:
    """007 FR-003: only the call that flips active→cancelled gets the booking back."""

    await _room()
    booking = await repo.create_booking("Room 12", D1, D2, PHONE_A)

    assert await repo.cancel_booking(booking.reference, PHONE_A) is not None
    assert await repo.cancel_booking(booking.reference, PHONE_A) is None  # repeat → None
    assert await repo.cancel_booking("BK-NOPE", PHONE_A) is None  # unknown → None


async def test_offer_room_types_whatsapp_sends_list_and_returns_sentinel(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import tools
    from app.db.documents import RoomType

    await _room("Room 12", "double")
    await _room("Room 5", "single")
    inactive = await _room("Room 9", "suite")
    inactive.is_active = False
    await inactive.save()

    sent: list[tuple[str, dict]] = []

    async def fake_send(to: str, capacities: dict) -> dict:
        sent.append((to, capacities))
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(tools, "send_room_type_list", fake_send)

    result = await tools._offer_room_types_impl("whatsapp", "+1000000001")

    assert "tappable list" in result  # sentinel: don't re-enumerate
    assert len(sent) == 1
    to, capacities = sent[0]
    assert to == "+1000000001"
    # only active types, enum declaration order, with sleeps-capacity
    assert list(capacities.keys()) == [RoomType.single, RoomType.double]
    assert capacities[RoomType.double] == 2


async def test_offer_room_types_voice_and_api_enumerate_without_sending(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import tools

    await _room("Room 12", "double")

    async def unexpected_send(to: str, capacities: dict) -> dict:  # pragma: no cover
        raise AssertionError("no list message may be sent on voice/api channels")

    monkeypatch.setattr(tools, "send_room_type_list", unexpected_send)

    for channel in ("voice", "api"):
        result = await tools._offer_room_types_impl(channel, "+1000000001")
        assert "double (sleeps 2)" in result
        assert "suite" not in result  # no active suite rooms


async def test_offer_room_types_send_failure_falls_back_to_text(
    db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.agent import tools

    await _room("Room 12", "double")

    async def failing_send(to: str, capacities: dict) -> dict:
        raise RuntimeError("boom")

    monkeypatch.setattr(tools, "send_room_type_list", failing_send)

    with caplog.at_level("ERROR", logger="app.agent.tools"):
        result = await tools._offer_room_types_impl("whatsapp", "+1000000001")

    assert "double (sleeps 2)" in result  # conversation still works
    assert any("room_type_list.send_failed" in r.getMessage() for r in caplog.records)


async def _imaged_room(name: str, room_type: str = "double") -> Room:
    return await Room(
        name=name, room_type=room_type, capacity=2,
        image_url=f"https://images.example.com/{name.replace(' ', '-')}.jpg",
    ).insert()


async def test_check_availability_whatsapp_sends_carousel_and_sentinel(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import tools

    await _imaged_room("Room 201")
    await _imaged_room("Room 204", "deluxe")

    sent: list[tuple[str, list, str]] = []

    async def fake_carousel(to, rooms, bot_number):
        sent.append((to, list(rooms), bot_number))
        return {"messages": [{"id": "wamid.out"}]}

    monkeypatch.setattr(tools, "send_room_carousel", fake_carousel)

    result = await tools._check_availability_impl(
        "whatsapp", "+1000000001", "15551234567", str(D1), str(D2)
    )

    assert "carousel" in result and "Room 201" in result and "Room 204" in result
    assert len(sent) == 1
    to, rooms, bot_number = sent[0]
    assert to == "+1000000001" and bot_number == "15551234567"
    assert [r.name for r in rooms] == ["Room 201", "Room 204"]


async def test_check_availability_whatsapp_falls_back_below_two_imaged_rooms(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import tools

    await _imaged_room("Room 201")
    await _room("Room 5", "single")  # no image

    async def unexpected_carousel(to, rooms, bot_number):  # pragma: no cover
        raise AssertionError("carousel must not be sent with <2 imaged rooms")

    monkeypatch.setattr(tools, "send_room_carousel", unexpected_carousel)

    result = await tools._check_availability_impl(
        "whatsapp", "+1000000001", "15551234567", str(D1), str(D2)
    )
    assert result.startswith("Available rooms:")
    assert "Room 201" in result and "Room 5" in result


async def test_check_availability_carousel_failure_falls_back_to_text(
    db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.agent import tools

    await _imaged_room("Room 201")
    await _imaged_room("Room 204", "deluxe")

    async def failing_carousel(to, rooms, bot_number):
        raise RuntimeError("boom")

    monkeypatch.setattr(tools, "send_room_carousel", failing_carousel)

    with caplog.at_level("ERROR", logger="app.agent.tools"):
        result = await tools._check_availability_impl(
            "whatsapp", "+1000000001", "15551234567", str(D1), str(D2)
        )

    assert result.startswith("Available rooms:")  # conversation still works
    assert any("room_carousel.send_failed" in r.getMessage() for r in caplog.records)


async def test_check_availability_voice_and_api_never_send_carousel(
    db, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agent import tools

    await _imaged_room("Room 201")
    await _imaged_room("Room 204", "deluxe")

    async def unexpected_carousel(to, rooms, bot_number):  # pragma: no cover
        raise AssertionError("carousel is whatsapp-only")

    monkeypatch.setattr(tools, "send_room_carousel", unexpected_carousel)

    for channel in ("voice", "api"):
        result = await tools._check_availability_impl(
            channel, "+1000000001", None, str(D1), str(D2)
        )
        assert result.startswith("Available rooms:")


async def test_offer_room_types_empty_catalog(db) -> None:
    from app.agent import tools

    result = await tools._offer_room_types_impl("whatsapp", "+1000000001")
    assert "no room types" in result.lower()


async def test_list_bookings_scoped_to_caller(db) -> None:
    await _room("Room 12")
    await _room("Room 5", "single")
    await repo.create_booking("Room 12", D1, D2, PHONE_A)
    await repo.create_booking("Room 5", D1, D2, PHONE_B)

    a = await repo.list_bookings(PHONE_A)
    assert len(a) == 1 and a[0].phone_number == PHONE_A
    assert await repo.list_bookings("+1999999999") == []
