"""WhatsApp API endpoint routes."""

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File

from config import WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_VERIFY_TOKEN
from models.whatsapp import (
    CreateTemplateRequest,
    MediaUploadResponse,
    MessageResponse,
    SendMediaRequest,
    SendMessageRequest,
    SendTemplateRequest,
    TemplateResponse,
)
from services.whatsapp import (
    _save_message,
    create_template,
    delete_template,
    get_templates,
    messages_store,
    send_media_message,
    send_template_message,
    send_text_message,
    upload_media,
)

router = APIRouter(prefix="/whatsapp", tags=["WhatsApp"])


# -- Webhook ---------------------------------------------------------------

@router.get("/webhook")
async def verify_webhook(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
):
    if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
        return int(hub_challenge) if hub_challenge.isdigit() else hub_challenge
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/webhook")
async def receive_webhook(request: Request):
    print(">>> HIT: POST /whatsapp/webhook")
    try:
        payload = await request.json()
    except Exception:
        print(">>> Invalid JSON at POST /whatsapp/webhook")
        return {"status": "error"}

    from services.webhook_handler import handle_webhook
    await handle_webhook(payload, source="/whatsapp/webhook")
    return {"status": "ok"}


# -- Send Messages ----------------------------------------------------------

@router.post("/send", response_model=MessageResponse)
async def send_text(req: SendMessageRequest):
    try:
        data = await send_text_message(req.to, req.body)
        wa_id = data.get("messages", [{}])[0].get("id", "")
        return MessageResponse(success=True, wa_message_id=wa_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/send-template", response_model=MessageResponse)
async def send_template(req: SendTemplateRequest):
    try:
        data = await send_template_message(
            req.to, req.template_name, req.language_code, req.components
        )
        wa_id = data.get("messages", [{}])[0].get("id", "")
        return MessageResponse(success=True, wa_message_id=wa_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/send-media", response_model=MessageResponse)
async def send_media(req: SendMediaRequest):
    try:
        data = await send_media_message(
            req.to, req.media_type, req.media_url, req.media_id, req.caption
        )
        wa_id = data.get("messages", [{}])[0].get("id", "")
        return MessageResponse(success=True, wa_message_id=wa_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# -- Media Upload -----------------------------------------------------------

@router.post("/upload-media", response_model=MediaUploadResponse)
async def upload_media_file(file: UploadFile = File(...)):
    try:
        contents = await file.read()
        media_id = await upload_media(contents, file.content_type or "application/octet-stream")
        return MediaUploadResponse(media_id=media_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# -- Templates --------------------------------------------------------------

@router.get("/templates", response_model=TemplateResponse)
async def list_templates():
    try:
        data = await get_templates()
        return TemplateResponse(success=True, data=data.get("data", []))
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.post("/templates", response_model=TemplateResponse)
async def create_new_template(req: CreateTemplateRequest):
    try:
        payload = {
            "name": req.name,
            "category": req.category,
            "language": req.language,
            "components": req.components,
        }
        data = await create_template(payload)
        return TemplateResponse(success=True, data=data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@router.delete("/templates/{template_name}", response_model=TemplateResponse)
async def delete_existing_template(template_name: str):
    try:
        data = await delete_template(template_name)
        return TemplateResponse(success=True, data=data)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


# -- Stored Messages (in-memory) -------------------------------------------

@router.get("/messages")
async def list_messages(
    phone: str | None = Query(None),
    direction: str | None = Query(None),
):
    results = messages_store
    if phone:
        results = [m for m in results if m["sender"] == phone or m["recipient"] == phone]
    if direction:
        results = [m for m in results if m["direction"] == direction]
    return list(reversed(results))


@router.get("/messages/{message_id}")
async def get_message(message_id: str):
    for msg in messages_store:
        if msg["id"] == message_id:
            return msg
    raise HTTPException(status_code=404, detail="Message not found")
