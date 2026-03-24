"""CRUD operations for the ``customers`` MongoDB collection.

Every function is async and takes the Motor database handle as its
first parameter.  The ``whatsapp_number`` field is the primary
identifier used by the WhatsApp chatbot.
"""

from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from models.common import utc_now
from models.customer import CustomerCreate, CustomerInDB, CustomerUpdate

COLLECTION = "customers"


async def create_customer(db: AsyncIOMotorDatabase, data: CustomerCreate) -> CustomerInDB:
    """Insert a new customer document.

    Args:
        db: Motor database handle.
        data: Validated customer creation payload.

    Returns:
        The newly created customer document.
    """
    now = utc_now()
    doc: dict[str, Any] = {
        "customer_id": uuid4().hex[:12],
        **data.model_dump(),
        "total_bookings": 0,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return CustomerInDB(**doc)


async def get_customer_by_whatsapp_number(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
) -> CustomerInDB | None:
    """Fetch a customer by their WhatsApp phone number.

    This is the **primary lookup** used by the WhatsApp chatbot on
    every incoming message.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number in international format (no ``+``
            prefix).

    Returns:
        The customer document, or ``None`` if not found.
    """
    number = whatsapp_number.strip().lstrip("+")
    doc = await db[COLLECTION].find_one({"whatsapp_number": number})
    return CustomerInDB(**doc) if doc else None


async def get_customer_by_id(
    db: AsyncIOMotorDatabase,
    customer_id: str,
) -> CustomerInDB | None:
    """Fetch a customer by their ``customer_id``.

    Args:
        db: Motor database handle.
        customer_id: The unique customer identifier.

    Returns:
        The customer document, or ``None`` if not found.
    """
    doc = await db[COLLECTION].find_one({"customer_id": customer_id})
    return CustomerInDB(**doc) if doc else None


async def update_customer(
    db: AsyncIOMotorDatabase,
    customer_id: str,
    data: CustomerUpdate,
) -> CustomerInDB | None:
    """Update an existing customer document.

    Only fields present (non-``None``) in *data* are written.

    Args:
        db: Motor database handle.
        customer_id: The customer to update.
        data: Partial update payload.

    Returns:
        The updated customer document, or ``None`` if not found.
    """
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    if not updates:
        return await get_customer_by_id(db, customer_id)
    updates["updated_at"] = utc_now()
    result = await db[COLLECTION].find_one_and_update(
        {"customer_id": customer_id},
        {"$set": updates},
        return_document=True,
    )
    return CustomerInDB(**result) if result else None


async def upsert_customer(
    db: AsyncIOMotorDatabase,
    whatsapp_number: str,
    full_name: str = "",
    email: str = "",
    nationality: str = "",
) -> CustomerInDB:
    """Create the customer if they don't exist, otherwise update.

    This is the function called on every incoming WhatsApp message to
    ensure the customer record exists.

    Args:
        db: Motor database handle.
        whatsapp_number: Phone number in international format.
        full_name: Customer's name (updated if provided).
        email: Customer's e-mail (updated if provided).
        nationality: Customer's nationality (updated if provided).

    Returns:
        The upserted customer document.
    """
    number = whatsapp_number.strip().lstrip("+")
    now = utc_now()

    # Build $set for fields that should always be updated
    set_on_update: dict[str, Any] = {"updated_at": now}
    if full_name:
        set_on_update["full_name"] = full_name
    if email:
        set_on_update["email"] = email
    if nationality:
        set_on_update["nationality"] = nationality

    # Build $setOnInsert for fields only set on creation
    set_on_insert: dict[str, Any] = {
        "customer_id": uuid4().hex[:12],
        "whatsapp_number": number,
        "total_bookings": 0,
        "created_at": now,
    }
    # Include defaults for optional fields only on insert
    if not full_name:
        set_on_insert["full_name"] = ""
    if not email:
        set_on_insert["email"] = ""
    if not nationality:
        set_on_insert["nationality"] = ""

    result = await db[COLLECTION].find_one_and_update(
        {"whatsapp_number": number},
        {
            "$set": set_on_update,
            "$setOnInsert": set_on_insert,
        },
        upsert=True,
        return_document=True,
    )
    return CustomerInDB(**result)
