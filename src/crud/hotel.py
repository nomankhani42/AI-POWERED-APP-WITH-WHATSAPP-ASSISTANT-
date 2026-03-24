"""CRUD operations for the ``hotels`` MongoDB collection.

Every function is async and takes the Motor database handle as its
first parameter.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.common import utc_now
from models.hotel import HotelCreate, HotelInDB, HotelUpdate

COLLECTION = "hotels"


async def create_hotel(db: AsyncIOMotorDatabase, data: HotelCreate) -> HotelInDB:
    """Insert a new hotel document.

    Args:
        db: Motor database handle.
        data: Validated hotel creation payload.

    Returns:
        The newly created hotel document.
    """
    now = utc_now()
    doc: dict[str, Any] = {
        "hotel_id": uuid4().hex[:12],
        **data.model_dump(),
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return HotelInDB(**doc)


async def get_hotel_by_id(db: AsyncIOMotorDatabase, hotel_id: str) -> HotelInDB | None:
    """Fetch a single hotel by its ``hotel_id``.

    Args:
        db: Motor database handle.
        hotel_id: The unique hotel identifier.

    Returns:
        The hotel document, or ``None`` if not found.
    """
    doc = await db[COLLECTION].find_one({"hotel_id": hotel_id, "is_active": True})
    return HotelInDB(**doc) if doc else None


async def get_all_hotels(
    db: AsyncIOMotorDatabase,
    skip: int = 0,
    limit: int = 50,
    active_only: bool = True,
) -> list[HotelInDB]:
    """Return a paginated list of hotels.

    Args:
        db: Motor database handle.
        skip: Number of documents to skip (offset).
        limit: Maximum documents to return.
        active_only: If ``True``, exclude soft-deleted hotels.

    Returns:
        List of hotel documents.
    """
    query: dict[str, Any] = {}
    if active_only:
        query["is_active"] = True
    cursor = db[COLLECTION].find(query).skip(skip).limit(limit).sort("created_at", -1)
    return [HotelInDB(**doc) async for doc in cursor]


async def update_hotel(
    db: AsyncIOMotorDatabase,
    hotel_id: str,
    data: HotelUpdate,
) -> HotelInDB | None:
    """Update an existing hotel document.

    Only fields present (non-``None``) in *data* are written.

    Args:
        db: Motor database handle.
        hotel_id: The hotel to update.
        data: Partial update payload.

    Returns:
        The updated hotel document, or ``None`` if not found.
    """
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return await get_hotel_by_id(db, hotel_id)
    updates["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"hotel_id": hotel_id, "is_active": True},
        {"$set": updates},
        return_document=True,
    )
    return HotelInDB(**result) if result else None


async def delete_hotel(db: AsyncIOMotorDatabase, hotel_id: str) -> bool:
    """Soft-delete a hotel by setting ``is_active`` to ``False``.

    Args:
        db: Motor database handle.
        hotel_id: The hotel to delete.

    Returns:
        ``True`` if the hotel was found and deactivated, ``False``
        otherwise.
    """
    result = await db[COLLECTION].update_one(
        {"hotel_id": hotel_id, "is_active": True},
        {"$set": {"is_active": False, "updated_at": utc_now()}},
    )
    return result.modified_count > 0
