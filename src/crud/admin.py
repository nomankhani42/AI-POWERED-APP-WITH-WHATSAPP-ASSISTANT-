"""CRUD operations for the ``admins`` MongoDB collection."""

from typing import Any
from uuid import uuid4

import bcrypt
from motor.motor_asyncio import AsyncIOMotorDatabase

from models.admin import AdminCreate, AdminInDB
from models.common import utc_now

COLLECTION = "admins"


def _hash_password(password: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _verify_password(password: str, password_hash: str) -> bool:
    """Verify a plain-text password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), password_hash.encode())


async def create_admin(db: AsyncIOMotorDatabase, data: AdminCreate) -> AdminInDB:
    """Create a new admin account.

    Raises ValueError if an admin with the same email already exists.
    """
    existing = await db[COLLECTION].find_one({"email": data.email})
    if existing:
        raise ValueError("An admin with this email already exists")

    now = utc_now()
    doc: dict[str, Any] = {
        "admin_id": uuid4().hex[:12],
        "full_name": data.full_name,
        "email": data.email,
        "password_hash": _hash_password(data.password),
        "phone": data.phone,
        "role": data.role.value,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }
    result = await db[COLLECTION].insert_one(doc)
    doc["_id"] = result.inserted_id
    return AdminInDB(**doc)


async def authenticate_admin(
    db: AsyncIOMotorDatabase,
    email: str,
    password: str,
) -> AdminInDB | None:
    """Verify email + password. Returns admin if valid, None otherwise."""
    doc = await db[COLLECTION].find_one({"email": email.strip().lower()})
    if not doc:
        return None
    if not _verify_password(password, doc["password_hash"]):
        return None
    if not doc.get("is_active", True):
        return None
    return AdminInDB(**doc)


async def get_admin_by_id(
    db: AsyncIOMotorDatabase,
    admin_id: str,
) -> AdminInDB | None:
    """Fetch an admin by their admin_id."""
    doc = await db[COLLECTION].find_one({"admin_id": admin_id})
    return AdminInDB(**doc) if doc else None


async def get_admin_by_email(
    db: AsyncIOMotorDatabase,
    email: str,
) -> AdminInDB | None:
    """Fetch an admin by their email."""
    doc = await db[COLLECTION].find_one({"email": email.strip().lower()})
    return AdminInDB(**doc) if doc else None


async def list_admins(db: AsyncIOMotorDatabase) -> list[AdminInDB]:
    """Return all admin accounts."""
    docs = await db[COLLECTION].find().sort("created_at", -1).to_list(length=100)
    return [AdminInDB(**d) for d in docs]
