"""Pydantic v2 models for the Customer entity.

Each customer document lives in the ``customers`` MongoDB collection.
The ``whatsapp_number`` field is the primary identifier used by the
WhatsApp chatbot — it is unique-indexed in MongoDB.
"""

from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from models.common import MongoBaseModel, PyObjectId, utc_now


# ---------------------------------------------------------------------------
# Core document model
# ---------------------------------------------------------------------------

class CustomerInDB(MongoBaseModel):
    """Full customer document as persisted in MongoDB.

    Attributes:
        customer_id: Unique customer identifier.
        whatsapp_number: The customer's WhatsApp phone number in
            international format (e.g. ``"14155551234"``).  This is the
            primary identifier for bot interactions.
        full_name: Customer's display name.
        email: Optional e-mail address.
        nationality: Optional nationality / country code.
        total_bookings: Running count of bookings made by this customer.
        created_at: UTC creation timestamp.
        updated_at: UTC last-update timestamp.
    """

    customer_id: str = Field(..., description="Unique customer identifier")
    whatsapp_number: str = Field(
        ...,
        min_length=7,
        max_length=20,
        description="WhatsApp phone number (international format)",
    )
    full_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    nationality: str = Field(default="", max_length=100)
    total_bookings: int = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)
    updated_at: datetime = Field(default_factory=utc_now)

    @field_validator("whatsapp_number")
    @classmethod
    def strip_whatsapp_number(cls, v: str) -> str:
        """Remove leading ``+`` and whitespace."""
        return v.strip().lstrip("+")


# ---------------------------------------------------------------------------
# Create / Update schemas
# ---------------------------------------------------------------------------

class CustomerCreate(BaseModel):
    """Payload accepted when creating a new customer."""

    whatsapp_number: str = Field(
        ..., min_length=7, max_length=20,
        description="WhatsApp phone number (international format)",
    )
    full_name: str = Field(default="", max_length=200)
    email: str = Field(default="", max_length=254)
    nationality: str = Field(default="", max_length=100)

    @field_validator("whatsapp_number")
    @classmethod
    def strip_whatsapp_number(cls, v: str) -> str:
        return v.strip().lstrip("+")


class CustomerUpdate(BaseModel):
    """Payload accepted when updating an existing customer.

    All fields are optional — only supplied fields are written.
    """

    full_name: str | None = Field(default=None, max_length=200)
    email: str | None = Field(default=None, max_length=254)
    nationality: str | None = Field(default=None, max_length=100)
