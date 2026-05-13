"""Pydantic v2 models for the Admin entity.

Admin documents are stored in the ``admins`` MongoDB collection.
Roles: super_admin, admin, manager, user (for future multi-role auth).
"""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field, field_validator

from models.common import MongoBaseModel, utc_now


class AdminRole(str, Enum):
    """Available admin roles — ordered by privilege level."""
    SUPER_ADMIN = "super_admin"
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"


class AdminInDB(MongoBaseModel):
    """Full admin document as persisted in MongoDB."""

    admin_id: str = Field(..., description="Unique admin identifier")
    full_name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=254)
    password_hash: str = Field(..., description="Bcrypt hashed password")
    phone: str = Field(default="", max_length=20)
    role: AdminRole = Field(default=AdminRole.USER, description="User role")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.strip().lower()


class AdminCreate(BaseModel):
    """Payload for creating a new admin account."""

    full_name: str = Field(..., min_length=2, max_length=200)
    email: str = Field(..., min_length=5, max_length=254)
    password: str = Field(..., min_length=6, max_length=128)
    phone: str = Field(default="", max_length=20)
    role: AdminRole = Field(default=AdminRole.USER, description="User role")

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.strip().lower()


class AdminLogin(BaseModel):
    """Payload for admin login."""

    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=128)

    @field_validator("email")
    @classmethod
    def lowercase_email(cls, v: str) -> str:
        return v.strip().lower()


class AdminResponse(BaseModel):
    """Safe admin response (no password hash)."""

    admin_id: str
    full_name: str
    email: str
    phone: str
    role: AdminRole
    is_active: bool
    created_at: datetime
    updated_at: datetime
