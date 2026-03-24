"""CRUD operations for the ``rooms`` MongoDB collection.

Every function is async and takes the Motor database handle as its
first parameter.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.common import utc_now
from models.room import RoomCreate, RoomInDB, RoomUpdate

COLLECTION = "rooms"
BOOKINGS_COLLECTION = "bookings"


async def create_room(db: AsyncIOMotorDatabase, data: RoomCreate) -> RoomInDB:
    """Insert a new room document.

    Args:
        db: Motor database handle.
        data: Validated room creation payload.

    Returns:
        The newly created room document.
    """
    now = utc_now()
    doc: dict[str, Any] = {
        "room_id": uuid4().hex[:12],
        **data.model_dump(),
        "is_available": True,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return RoomInDB(**doc)


async def get_room_by_id(db: AsyncIOMotorDatabase, room_id: str) -> RoomInDB | None:
    """Fetch a single room by its ``room_id``.

    Args:
        db: Motor database handle.
        room_id: The unique room identifier.

    Returns:
        The room document, or ``None`` if not found.
    """
    doc = await db[COLLECTION].find_one({"room_id": room_id, "is_available": {"$exists": True}})
    return RoomInDB(**doc) if doc else None


async def get_rooms_by_hotel(
    db: AsyncIOMotorDatabase,
    hotel_id: str,
    skip: int = 0,
    limit: int = 50,
) -> list[RoomInDB]:
    """Return all rooms belonging to a specific hotel.

    Args:
        db: Motor database handle.
        hotel_id: Parent hotel identifier.
        skip: Number of documents to skip.
        limit: Maximum documents to return.

    Returns:
        List of room documents for the hotel.
    """
    cursor = (
        db[COLLECTION]
        .find({"hotel_id": hotel_id})
        .skip(skip)
        .limit(limit)
        .sort("room_number", 1)
    )
    return [RoomInDB(**doc) async for doc in cursor]


async def get_available_rooms(
    db: AsyncIOMotorDatabase,
    hotel_id: str,
    check_in: datetime,
    check_out: datetime,
) -> list[RoomInDB]:
    """Return rooms available for the given date range at a hotel.

    A room is considered available if:
    1. ``is_available`` is ``True``.
    2. No *active* booking (status in ``pending``, ``confirmed``)
       overlaps the requested date range.

    Args:
        db: Motor database handle.
        hotel_id: Hotel to search in.
        check_in: Desired check-in datetime.
        check_out: Desired check-out datetime.

    Returns:
        List of available room documents.
    """
    # Step 1: Find room_ids that have overlapping active bookings
    booked_cursor = db[BOOKINGS_COLLECTION].find(
        {
            "hotel_id": hotel_id,
            "status": {"$in": ["pending", "confirmed"]},
            "check_in_date": {"$lt": check_out},
            "check_out_date": {"$gt": check_in},
        },
        {"room_id": 1},
    )
    booked_room_ids: list[str] = [
        doc["room_id"] async for doc in booked_cursor
    ]

    # Step 2: Return rooms NOT in the booked set
    query: dict[str, Any] = {
        "hotel_id": hotel_id,
        "is_available": True,
    }
    if booked_room_ids:
        query["room_id"] = {"$nin": booked_room_ids}

    cursor = db[COLLECTION].find(query).sort("price_per_session", 1)
    return [RoomInDB(**doc) async for doc in cursor]


async def update_room(
    db: AsyncIOMotorDatabase,
    room_id: str,
    data: RoomUpdate,
) -> RoomInDB | None:
    """Update an existing room document.

    Only fields present (non-``None``) in *data* are written.

    Args:
        db: Motor database handle.
        room_id: The room to update.
        data: Partial update payload.

    Returns:
        The updated room document, or ``None`` if not found.
    """
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return await get_room_by_id(db, room_id)
    updates["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"room_id": room_id},
        {"$set": updates},
        return_document=True,
    )
    return RoomInDB(**result) if result else None


async def delete_room(db: AsyncIOMotorDatabase, room_id: str) -> bool:
    """Soft-delete a room by setting ``is_available`` to ``False``.

    Args:
        db: Motor database handle.
        room_id: The room to delete.

    Returns:
        ``True`` if the room was found and deactivated, ``False``
        otherwise.
    """
    result = await db[COLLECTION].update_one(
        {"room_id": room_id},
        {"$set": {"is_available": False, "updated_at": utc_now()}},
    )
    return result.modified_count > 0
