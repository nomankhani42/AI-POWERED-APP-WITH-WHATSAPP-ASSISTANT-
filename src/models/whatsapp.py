"""Pydantic models for WhatsApp message handling.

This module defines every request body, response body, and stored-document
schema used by the WhatsApp endpoints and services.

Sections:
    - **Stored document schema** – mirrors MongoDB ``messages`` documents.
    - **Request models** – validate incoming JSON payloads.
    - **Response models** – shape the JSON returned to API consumers.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


# --- Stored document schema ---

class WhatsAppMessage(BaseModel):
    """Schema that mirrors a single document in the MongoDB ``messages``
    collection.

    Attributes:
        sender (str): Phone number or ID of the message sender.
        recipient (str): Phone number or ID of the message recipient.
        message_body (str): The textual content of the message.
            Defaults to an empty string for non-text types.
        message_type (str): Type of WhatsApp message.
            One of ``"text"``, ``"template"``, ``"image"``,
            ``"document"``, ``"audio"``, ``"video"``.
            Defaults to ``"text"``.
        direction (str): Whether the message is ``"incoming"`` (received)
            or ``"outgoing"`` (sent by the agent).  Defaults to
            ``"incoming"``.
        wa_message_id (str): The unique message ID returned by the
            WhatsApp Cloud API.  Empty string if not yet available.
        timestamp (datetime): UTC timestamp of when the message was
            created.  Auto-populated via ``datetime.utcnow``.
        status (str): Delivery status of the message — ``"sent"``,
            ``"delivered"``, ``"read"``, or ``"failed"``.
            Defaults to ``"sent"``.
    """

    sender: str
    recipient: str
    message_body: str = ""
    message_type: str = "text"  # text, template, image, document, audio, video
    direction: str = "incoming"  # incoming | outgoing
    wa_message_id: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    status: str = "sent"  # sent, delivered, read, failed


# --- Request models ---

class SendMessageRequest(BaseModel):
    """Request body for sending a plain-text WhatsApp message.

    Attributes:
        to (str): Recipient phone number in international format
            (e.g. ``"14155551234"``).
        body (str): The text content of the message.
    """

    to: str
    body: str


class SendTemplateRequest(BaseModel):
    """Request body for sending a pre-approved WhatsApp template message.

    Attributes:
        to (str): Recipient phone number in international format.
        template_name (str): The exact name of the approved template
            as registered in the Meta Business Manager.
        language_code (str): BCP-47 language/locale code for the
            template.  Defaults to ``"en_US"``.
        components (list[dict[str, Any]]): Optional list of template
            component objects (header, body, button parameters) as
            defined by the WhatsApp Cloud API.  Defaults to an empty
            list.
    """

    to: str
    template_name: str
    language_code: str = "en_US"
    components: list[dict[str, Any]] = []


class SendMediaRequest(BaseModel):
    """Request body for sending a media message (image, video, etc.).

    Provide **either** ``media_url`` (a publicly accessible link) or
    ``media_id`` (a previously uploaded media asset ID).  If both are
    given, ``media_id`` takes precedence.

    Attributes:
        to (str): Recipient phone number in international format.
        media_type (str): Kind of media being sent — ``"image"``,
            ``"document"``, ``"audio"``, or ``"video"``.
        media_url (str | None): Public URL of the media file.
            ``None`` if using ``media_id`` instead.
        media_id (str | None): Meta media ID obtained from a prior
            upload.  ``None`` if using ``media_url`` instead.
        caption (str | None): Optional caption displayed alongside the
            media.  ``None`` for no caption.
    """

    to: str
    media_type: str  # image, document, audio, video
    media_url: str | None = None
    media_id: str | None = None
    caption: str | None = None


class CreateTemplateRequest(BaseModel):
    """Request body for creating a new WhatsApp message template.

    Attributes:
        name (str): Unique template name (lowercase, underscores
            allowed, no spaces).
        category (str): Template category as required by Meta —
            ``"MARKETING"``, ``"UTILITY"``, or ``"AUTHENTICATION"``.
        language (str): BCP-47 language/locale code.
            Defaults to ``"en_US"``.
        components (list[dict[str, Any]]): List of component objects
            (HEADER, BODY, FOOTER, BUTTONS) describing the template
            layout.  Defaults to an empty list.
    """

    name: str
    category: str  # MARKETING, UTILITY, AUTHENTICATION
    language: str = "en_US"
    components: list[dict[str, Any]] = []


# --- Response models ---

class MessageResponse(BaseModel):
    """Standard response returned after attempting to send a WhatsApp
    message.

    Attributes:
        success (bool): ``True`` if the message was accepted by the
            WhatsApp Cloud API; ``False`` otherwise.
        wa_message_id (str | None): The message ID assigned by
            WhatsApp.  ``None`` when the send failed.
        detail (str): Human-readable detail or error description.
            Empty string on success.
    """

    success: bool
    wa_message_id: str | None = None
    detail: str = ""


class MediaUploadResponse(BaseModel):
    """Response returned after successfully uploading a media file.

    Attributes:
        media_id (str): The Meta media ID that can be referenced in
            subsequent ``send-media`` requests.
    """

    media_id: str


class TemplateResponse(BaseModel):
    """Generic response for template CRUD operations.

    Attributes:
        success (bool): ``True`` if the operation completed
            successfully.
        data (Any): Payload returned by the Meta API (template
            list, creation confirmation, etc.).  ``None`` if
            not applicable.
        detail (str): Human-readable detail or error description.
            Empty string on success.
    """

    success: bool
    data: Any = None
    detail: str = ""


class StoredMessageResponse(BaseModel):
    """Serialised representation of a message document retrieved from
    MongoDB.

    Attributes:
        id (str): The MongoDB ``_id`` as a hex string.
        sender (str): Phone number or ID of the sender.
        recipient (str): Phone number or ID of the recipient.
        message_body (str): Textual content (or placeholder like
            ``"[image]"`` for media messages).
        message_type (str): ``"text"``, ``"template"``, ``"image"``,
            ``"document"``, ``"audio"``, or ``"video"``.
        direction (str): ``"incoming"`` or ``"outgoing"``.
        wa_message_id (str): WhatsApp Cloud API message ID.
        timestamp (datetime): UTC time the message was recorded.
        status (str): Delivery status (``"sent"``, ``"delivered"``,
            ``"read"``, ``"received"``, ``"failed"``).
    """

    id: str
    sender: str
    recipient: str
    message_body: str
    message_type: str
    direction: str
    wa_message_id: str
    timestamp: datetime
    status: str
