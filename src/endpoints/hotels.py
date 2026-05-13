"""Hotels & Rooms REST API endpoints."""

from fastapi import APIRouter, HTTPException, Query

from database import get_db
from crud.hotel import get_all_hotels, get_hotel_by_id
from crud.room import get_rooms_by_hotel

router = APIRouter(prefix="/hotels", tags=["Hotels"])


@router.get("/")
async def list_hotels(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    """List all active hotels."""
    db = get_db()
    hotels = await get_all_hotels(db, skip=skip, limit=limit)
    return [h.model_dump(mode="json") for h in hotels]


@router.get("/stats")
async def hotel_stats():
    """Hotel & room counts."""
    db = get_db()
    hotel_count = await db["hotels"].count_documents({"is_active": True})
    room_count = await db["rooms"].count_documents({})
    available_rooms = await db["rooms"].count_documents({"is_available": True})
    return {
        "total_hotels": hotel_count,
        "total_rooms": room_count,
        "available_rooms": available_rooms,
    }


@router.get("/{hotel_id}")
async def get_hotel(hotel_id: str):
    db = get_db()
    hotel = await get_hotel_by_id(db, hotel_id)
    if not hotel:
        raise HTTPException(status_code=404, detail="Hotel not found")
    return hotel.model_dump(mode="json")


@router.get("/{hotel_id}/rooms")
async def list_rooms(hotel_id: str):
    """List all rooms for a hotel."""
    db = get_db()
    rooms = await get_rooms_by_hotel(db, hotel_id)
    return [r.model_dump(mode="json") for r in rooms]
