"""Unit tests for booking tool WhatsApp notifications."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from app.agent import tools
from app.db import bookings as repo
from app.db.documents import BookingStatus

PHONE = "+15550001111"


def _booking(status: BookingStatus = BookingStatus.active) -> SimpleNamespace:
    return SimpleNamespace(
        reference="BK-1234ABCD",
        room_name="Room 12",
        check_in=date(2026, 8, 1),
        check_out=date(2026, 8, 3),
        status=status,
    )


async def test_book_room_sends_confirmation_after_success(monkeypatch) -> None:
    booking = _booking()
    sent: list[tuple[str, object]] = []

    async def create_booking(room_name, check_in, check_out, phone_number, guest_name=None):
        assert (room_name, check_in, check_out, phone_number, guest_name) == (
            "Room 12",
            date(2026, 8, 1),
            date(2026, 8, 3),
            PHONE,
            "Aisha",
        )
        return booking

    async def send_confirmation(to, created_booking):
        sent.append((to, created_booking))

    monkeypatch.setattr(tools.repo, "create_booking", create_booking)
    monkeypatch.setattr(tools, "send_booking_confirmation", send_confirmation)

    reply = await tools._book_room_impl(PHONE, "Room 12", "2026-08-01", "2026-08-03", "Aisha")

    assert sent == [(PHONE, booking)]
    assert reply == "Booked Room 12 from 2026-08-01 to 2026-08-03. Your booking reference is BK-1234ABCD."


async def test_book_room_does_not_notify_when_booking_fails(monkeypatch) -> None:
    sent: list[tuple[str, object]] = []

    async def create_booking(*args, **kwargs):
        raise repo.BookingError("Room is unavailable.")

    async def send_confirmation(to, created_booking):
        sent.append((to, created_booking))

    monkeypatch.setattr(tools.repo, "create_booking", create_booking)
    monkeypatch.setattr(tools, "send_booking_confirmation", send_confirmation)

    reply = await tools._book_room_impl(PHONE, "Room 12", "2026-08-01", "2026-08-03")

    assert reply == "Room is unavailable."
    assert sent == []


async def test_book_room_still_returns_success_when_notification_fails(monkeypatch) -> None:
    booking = _booking()

    async def create_booking(*args, **kwargs):
        return booking

    async def send_confirmation(to, created_booking):
        raise RuntimeError("network down")

    monkeypatch.setattr(tools.repo, "create_booking", create_booking)
    monkeypatch.setattr(tools, "send_booking_confirmation", send_confirmation)

    reply = await tools._book_room_impl(PHONE, "Room 12", "2026-08-01", "2026-08-03")

    assert "Your booking reference is BK-1234ABCD" in reply


async def test_cancel_booking_sends_cancellation_after_success(monkeypatch) -> None:
    booking = _booking(BookingStatus.cancelled)
    sent: list[tuple[str, object]] = []

    async def cancel_booking(reference, phone_number):
        assert (reference, phone_number) == ("BK-1234ABCD", PHONE)
        return booking

    async def send_cancellation(to, cancelled_booking):
        sent.append((to, cancelled_booking))

    monkeypatch.setattr(tools.repo, "cancel_booking", cancel_booking)
    monkeypatch.setattr(tools, "send_booking_cancellation", send_cancellation)

    reply = await tools._cancel_booking_impl(PHONE, "BK-1234ABCD")

    assert sent == [(PHONE, booking)]
    assert reply == "Booking BK-1234ABCD is now cancelled."


async def test_cancel_booking_does_not_notify_when_not_found(monkeypatch) -> None:
    sent: list[tuple[str, object]] = []

    async def cancel_booking(reference, phone_number):
        return None

    async def send_cancellation(to, cancelled_booking):
        sent.append((to, cancelled_booking))

    monkeypatch.setattr(tools.repo, "cancel_booking", cancel_booking)
    monkeypatch.setattr(tools, "send_booking_cancellation", send_cancellation)

    reply = await tools._cancel_booking_impl(PHONE, "MISSING")

    assert reply == (
        "I couldn't find an active booking with reference MISSING under your "
        "number. It may already be cancelled."
    )
    assert sent == []
