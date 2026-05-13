"""Bookings REST API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import get_db
from models.booking import BookingStatus
from crud.booking import (
    get_all_bookings,
    get_booking_by_booking_id,
    get_bookings_by_customer,
    update_booking_status,
    cancel_booking,
)
from crud.customer import get_customer_by_id
from crud.admin import get_admin_by_id
from services.whatsapp import send_text_message
from services.push_notifications import send_push_notification

router = APIRouter(prefix="/bookings", tags=["Bookings"])

COLLECTION = "bookings"


class StatusUpdateRequest(BaseModel):
    status: BookingStatus


# -- Stats (must be before /{booking_id} to avoid path conflict) -----------

@router.get("/stats")
async def booking_stats():
    db = get_db()
    pipeline = [
        {"$group": {"_id": "$status", "count": {"$sum": 1}}},
    ]
    counts = {s.value: 0 for s in BookingStatus}
    total = 0
    async for doc in db[COLLECTION].aggregate(pipeline):
        status_val = doc["_id"]
        count = doc["count"]
        counts[status_val] = count
        total += count
    return {"total": total, **counts}


# -- User Bookings (by admin_id → customer phone/email) --------------------

@router.get("/user/{admin_id}")
async def list_user_bookings(
    admin_id: str,
    status: BookingStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """Fetch bookings for a user account.

    Looks up the admin by ID, then finds the matching customer
    record by phone or email, and returns their bookings.
    """
    db = get_db()
    admin = await get_admin_by_id(db, admin_id)
    if not admin:
        raise HTTPException(status_code=404, detail="User not found")

    # Try to find a customer matching this user's phone or email.
    # Mobile-app users may have their email stored as whatsapp_number
    # (the chat agent uses admin email as session key when phone is empty).
    customer = None
    if admin.phone:
        phone = admin.phone.strip().lstrip("+")
        customer = await db["customers"].find_one({"whatsapp_number": phone})
    if not customer and admin.email:
        customer = await db["customers"].find_one({"email": admin.email})
    if not customer and admin.email:
        # Mobile app chat uses email as whatsapp_number when phone is empty
        customer = await db["customers"].find_one({"whatsapp_number": admin.email})
    if not customer and admin.phone:
        # Also try matching email field with phone (edge case)
        phone = admin.phone.strip().lstrip("+")
        customer = await db["customers"].find_one({"email": phone})

    if not customer:
        return []  # No matching customer record → no bookings

    customer_id = customer["customer_id"]
    bookings = await get_bookings_by_customer(
        db, customer_id, skip=skip, limit=limit
    )

    # Filter by status if provided
    if status:
        bookings = [b for b in bookings if b.status == status]

    return [b.model_dump(mode="json") for b in bookings]


# -- List ------------------------------------------------------------------

@router.get("/")
async def list_bookings(
    status: BookingStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = get_db()
    bookings = await get_all_bookings(db, status=status, skip=skip, limit=limit)
    return [b.model_dump(mode="json") for b in bookings]


# -- Detail ----------------------------------------------------------------

@router.get("/{booking_id}")
async def get_booking(booking_id: str):
    db = get_db()
    booking = await get_booking_by_booking_id(db, booking_id)
    if not booking:
        raise HTTPException(status_code=404, detail="Booking not found")
    return booking.model_dump(mode="json")


# -- Status Update ---------------------------------------------------------

# WhatsApp notification messages per status
_NOTIFY_MESSAGES = {
    BookingStatus.CONFIRMED: (
        "✅ *Booking Confirmed!*\n\n"
        "Your booking *{booking_id}* has been confirmed.\n"
        "📅 Check-in: {check_in}\n"
        "👥 Guests: {guests}\n\n"
        "We look forward to welcoming you at The Grand Dine! 🍽️"
    ),
    BookingStatus.CANCELLED: (
        "❌ *Booking Cancelled*\n\n"
        "Your booking *{booking_id}* has been cancelled.\n\n"
        "If this was a mistake, feel free to message us to rebook. 🙏"
    ),
    BookingStatus.COMPLETED: (
        "🎉 *Booking Completed*\n\n"
        "Your booking *{booking_id}* is now marked as completed.\n"
        "Thank you for dining with us at The Grand Dine! ⭐\n\n"
        "We'd love to see you again soon!"
    ),
}


@router.patch("/{booking_id}/status")
async def patch_booking_status(booking_id: str, body: StatusUpdateRequest):
    db = get_db()
    if body.status == BookingStatus.CANCELLED:
        updated = await cancel_booking(db, booking_id)
    else:
        updated = await update_booking_status(db, booking_id, body.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Booking not found or status transition not allowed")

    # -- Send WhatsApp notification to the customer --
    template = _NOTIFY_MESSAGES.get(body.status)
    if template:
        try:
            customer = await get_customer_by_id(db, updated.customer_id)
            if customer and customer.whatsapp_number:
                check_in_str = updated.check_in_date.strftime("%b %d, %Y %I:%M %p")
                message = template.format(
                    booking_id=updated.booking_id,
                    check_in=check_in_str,
                    guests=updated.num_guests,
                )
                await send_text_message(to=customer.whatsapp_number, body=message)
                print(f">>> [Notify] Sent {body.status} notification to {customer.whatsapp_number}")
        except Exception as e:
            # Log but don't fail the status update if notification fails
            print(f">>> [Notify] Failed to send WhatsApp notification: {e}")

    # -- Send push notification to admin mobile app --
    status_emojis = {"confirmed": "✅", "cancelled": "❌", "completed": "🎉"}
    try:
        await send_push_notification(
            title=f"{status_emojis.get(body.status.value, '📋')} Booking {body.status.value.title()}",
            body=f"{updated.booking_id} — {updated.num_guests} guests | PKR {updated.total_price:,.0f}",
            data={"type": "status_change", "booking_id": updated.booking_id, "status": body.status.value},
        )
    except Exception as e:
        print(f">>> [Push] Failed to send status-change push: {e}")

    return updated.model_dump(mode="json")
