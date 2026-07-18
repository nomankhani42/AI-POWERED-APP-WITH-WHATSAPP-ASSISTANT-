"""Cancellation notification automation (007 US1, T006).

Exercises the shared tool path every channel uses (`_cancel_booking_impl`) against a real
MongoDB (skipped when unreachable), with the WhatsApp sender patched at the tools module
boundary. Contract: specs/007-cancel-message-room-select/contracts/cancellation-notification.md.
"""

from datetime import date, timedelta

import pytest
import pytest_asyncio

from app.agent import tools
from app.db import bookings as repo
from app.db.documents import Booking, BookingStatus, Room
from app.db.mongo import close_db, init_db
from app.services.whatsapp_messages import WhatsAppMessageError, booking_cancellation_text

D1 = date.today() + timedelta(days=10)
D2 = date.today() + timedelta(days=12)

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


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, object]]:
    """Record every cancellation send instead of calling the Graph API."""

    calls: list[tuple[str, object]] = []

    async def fake_send(to: str, booking: object) -> dict:
        calls.append((to, booking))
        return {"messages": [{"id": "wamid.sent"}]}

    monkeypatch.setattr(tools, "send_booking_cancellation", fake_send)
    return calls


async def _booked(phone: str = PHONE_A) -> Booking:
    await Room(name="Room 12", room_type="double", capacity=2).insert()
    return await repo.create_booking("Room 12", D1, D2, phone)


async def test_successful_cancel_sends_exactly_one_notice_with_details(db, sent) -> None:
    booking = await _booked()

    reply = await tools._cancel_booking_impl(PHONE_A, booking.reference)

    assert "cancelled" in reply
    assert len(sent) == 1
    to, sent_booking = sent[0]
    assert to == PHONE_A
    text = booking_cancellation_text(sent_booking)
    # FR-002: cancelled statement + reference + room + both dates
    assert "cancelled" in text
    assert booking.reference in text
    assert "Room 12" in text
    assert str(D1) in text and str(D2) in text


async def test_repeat_cancel_sends_nothing_more(db, sent) -> None:
    booking = await _booked()

    await tools._cancel_booking_impl(PHONE_A, booking.reference)
    reply = await tools._cancel_booking_impl(PHONE_A, booking.reference)

    assert len(sent) == 1  # FR-003: exactly once, ever
    assert "couldn't find" in reply.lower() or "already" in reply.lower()


async def test_rejected_attempts_send_nothing(db, sent) -> None:
    booking = await _booked()

    await tools._cancel_booking_impl(PHONE_A, "BK-DOESNOTEXIST")  # unknown reference
    await tools._cancel_booking_impl(PHONE_B, booking.reference)  # not this guest's

    assert sent == []
    fresh = await Booking.find_one(Booking.reference == booking.reference)
    assert fresh is not None and fresh.status == BookingStatus.active


async def test_delivery_failure_never_blocks_and_logs_structured_entry(
    db, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    booking = await _booked()

    async def failing_send(to: str, b: object) -> dict:
        raise WhatsAppMessageError("Graph message send failed: HTTP 401")

    monkeypatch.setattr(tools, "send_booking_cancellation", failing_send)

    with caplog.at_level("ERROR", logger="app.agent.tools"):
        reply = await tools._cancel_booking_impl(PHONE_A, booking.reference)

    # FR-004: cancellation stands and the guest still gets in-conversation confirmation.
    assert "cancelled" in reply
    fresh = await Booking.find_one(Booking.reference == booking.reference)
    assert fresh is not None and fresh.status == BookingStatus.cancelled

    failures = [r for r in caplog.records if "booking.cancellation_notice_failed" in r.getMessage()]
    assert len(failures) == 1
    message = failures[0].getMessage()
    assert booking.reference in message
    assert PHONE_A in message
    assert "WhatsAppMessageError" in message
