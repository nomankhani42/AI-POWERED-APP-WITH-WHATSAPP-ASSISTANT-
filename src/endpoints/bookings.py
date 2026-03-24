"""Bookings REST API endpoints."""

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from database import get_db
from models.booking import BookingStatus
from crud.booking import (
    get_all_bookings,
    get_booking_by_booking_id,
    update_booking_status,
    cancel_booking,
)

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

@router.patch("/{booking_id}/status")
async def patch_booking_status(booking_id: str, body: StatusUpdateRequest):
    db = get_db()
    if body.status == BookingStatus.CANCELLED:
        updated = await cancel_booking(db, booking_id)
    else:
        updated = await update_booking_status(db, booking_id, body.status)
    if not updated:
        raise HTTPException(status_code=404, detail="Booking not found or status transition not allowed")
    return updated.model_dump(mode="json")
