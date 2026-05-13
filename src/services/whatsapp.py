"""WhatsApp Cloud API service layer.

Wraps every Meta Graph API call needed by the application — sending
messages, uploading media, and managing message templates.  Each
public function is an ``async`` coroutine that uses ``httpx`` for
non-blocking HTTP communication.

Outgoing messages are stored in an in-memory list (``messages_store``).
"""

from datetime import datetime
from typing import Any
import io

import httpx

from config import (
    WHATSAPP_ACCESS_TOKEN,
    WHATSAPP_API_BASE,
    WHATSAPP_BUSINESS_ACCOUNT_ID,
    WHATSAPP_PHONE_NUMBER_ID,
)

# In-memory message store (replaces MongoDB for now)
messages_store: list[dict[str, Any]] = []


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}",
        "Content-Type": "application/json",
    }


def _save_message(
    sender: str,
    recipient: str,
    message_body: str,
    message_type: str,
    direction: str,
    wa_message_id: str,
    status: str = "sent",
) -> None:
    doc = {
        "id": str(len(messages_store) + 1),
        "sender": sender,
        "recipient": recipient,
        "message_body": message_body,
        "message_type": message_type,
        "direction": direction,
        "wa_message_id": wa_message_id,
        "timestamp": datetime.utcnow(),
        "status": status,
    }
    messages_store.append(doc)


async def send_text_message(to: str, body: str) -> dict[str, Any]:
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code != 200:
            error_body = resp.text
            print(f"Meta API error ({resp.status_code}): {error_body}")
            raise Exception(f"Meta API error ({resp.status_code}): {error_body}")
        data = resp.json()

    wa_message_id = data.get("messages", [{}])[0].get("id", "")
    _save_message(
        sender=WHATSAPP_PHONE_NUMBER_ID,
        recipient=to,
        message_body=body,
        message_type="text",
        direction="outgoing",
        wa_message_id=wa_message_id,
    )
    return data


async def send_template_message(
    to: str,
    template_name: str,
    language: str,
    components: list[dict[str, Any]],
) -> dict[str, Any]:
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    template_obj: dict[str, Any] = {
        "name": template_name,
        "language": {"code": language},
    }
    if components:
        template_obj["components"] = components
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": template_obj,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    wa_message_id = data.get("messages", [{}])[0].get("id", "")
    _save_message(
        sender=WHATSAPP_PHONE_NUMBER_ID,
        recipient=to,
        message_body=f"[template:{template_name}]",
        message_type="template",
        direction="outgoing",
        wa_message_id=wa_message_id,
    )
    return data


async def send_media_message(
    to: str,
    media_type: str,
    media_url: str | None,
    media_id: str | None,
    caption: str | None,
) -> dict[str, Any]:
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    media_obj: dict[str, Any] = {}
    if media_id:
        media_obj["id"] = media_id
    elif media_url:
        media_obj["link"] = media_url
    if caption:
        media_obj["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": media_type,
        media_type: media_obj,
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        data = resp.json()

    wa_message_id = data.get("messages", [{}])[0].get("id", "")
    _save_message(
        sender=WHATSAPP_PHONE_NUMBER_ID,
        recipient=to,
        message_body=caption or f"[{media_type}]",
        message_type=media_type,
        direction="outgoing",
        wa_message_id=wa_message_id,
    )
    return data


async def upload_media(file_bytes: bytes, mime_type: str) -> str:
    """Upload a media file to the WhatsApp Cloud API.

    The returned media ID can later be used in :func:`send_media_message`
    via the ``media_id`` parameter.

    Args:
        file_bytes (bytes): Raw binary content of the file to upload.
        mime_type (str): MIME type of the file (e.g.
            ``"image/jpeg"``, ``"application/pdf"``).

    Returns:
        str: The media ID assigned by Meta, usable in subsequent
            send-media requests.

    Raises:
        httpx.HTTPStatusError: If the Meta API returns a non-2xx
            status code.
    """
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/media"
    headers = {"Authorization": f"Bearer {WHATSAPP_ACCESS_TOKEN}"}
    files = {
        "file": ("upload", file_bytes, mime_type),
    }
    data = {"messaging_product": "whatsapp"}
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, files=files, data=data)
        resp.raise_for_status()
        result = resp.json()
    return result["id"]


async def get_templates() -> dict[str, Any]:
    """Retrieve all message templates for the WhatsApp Business Account.

    Calls ``GET /{WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates`` on
    the Meta Graph API.

    Returns:
        dict[str, Any]: The full JSON response, containing a ``data``
            list of template objects.

    Raises:
        httpx.HTTPStatusError: If the Meta API returns a non-2xx
            status code.
    """
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def create_template(payload: dict[str, Any]) -> dict[str, Any]:
    """Create a new message template in the WhatsApp Business Account.

    Args:
        payload (dict[str, Any]): The template definition matching the
            Meta Graph API schema.  Expected keys:

            - ``name`` (str) – Template name.
            - ``category`` (str) – ``"MARKETING"``, ``"UTILITY"``, or
              ``"AUTHENTICATION"``.
            - ``language`` (str) – BCP-47 language code.
            - ``components`` (list) – Template component objects.

    Returns:
        dict[str, Any]: The full JSON response from Meta, typically
            containing the new template ID and status.

    Raises:
        httpx.HTTPStatusError: If the Meta API returns a non-2xx
            status code.
    """
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def send_voice_note(to: str, text: str, voice: str = "nova") -> dict[str, Any]:
    """Generate TTS from text and send as a WhatsApp voice note (OGG/Opus).

    Args:
        to: Recipient WhatsApp phone number.
        text: Text to synthesise into speech.
        voice: OpenAI TTS voice (nova/shimmer = female, alloy/echo/fable/onyx = other).

    Returns:
        Meta API response dict.
    """
    from pydub import AudioSegment
    from services.ai_services.tts_openai import synthesise_to_bytes

    # TTS → raw PCM (24 kHz, 16-bit, mono)
    pcm_bytes = await synthesise_to_bytes(text, voice=voice)

    # PCM → OGG/Opus (WhatsApp voice note format)
    segment = AudioSegment(
        data=pcm_bytes,
        sample_width=2,
        frame_rate=24_000,
        channels=1,
    )
    ogg_buf = io.BytesIO()
    try:
        segment.export(ogg_buf, format="ogg", codec="libopus")
    except Exception:
        # Fallback: plain OGG without explicit codec
        ogg_buf = io.BytesIO()
        segment.export(ogg_buf, format="ogg")
    ogg_bytes = ogg_buf.getvalue()

    # Upload to Meta media endpoint
    media_id = await upload_media(ogg_bytes, "audio/ogg; codecs=opus")

    # Send as audio message
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "audio",
        "audio": {"id": media_id},
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, json=payload, headers=_headers())
        if resp.status_code != 200:
            raise Exception(f"Meta API error ({resp.status_code}): {resp.text}")
        data = resp.json()

    wa_message_id = data.get("messages", [{}])[0].get("id", "")
    _save_message(
        sender=WHATSAPP_PHONE_NUMBER_ID,
        recipient=to,
        message_body=f"[voice: {text[:60]}]",
        message_type="audio",
        direction="outgoing",
        wa_message_id=wa_message_id,
    )
    return data


async def delete_template(name: str) -> dict[str, Any]:
    """Delete a message template by name.

    Args:
        name (str): The exact name of the template to delete.  All
            language variants of the template will be removed.

    Returns:
        dict[str, Any]: The JSON response confirming deletion
            (typically ``{"success": true}``).

    Raises:
        httpx.HTTPStatusError: If the Meta API returns a non-2xx
            status code (e.g. template not found).
    """
    url = f"{WHATSAPP_API_BASE}/{WHATSAPP_BUSINESS_ACCOUNT_ID}/message_templates"
    async with httpx.AsyncClient() as client:
        resp = await client.delete(url, params={"name": name}, headers=_headers())
        resp.raise_for_status()
        return resp.json()
