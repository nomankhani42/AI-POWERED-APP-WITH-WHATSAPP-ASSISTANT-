"""Meta Graph API call control + webhook signature verification (T010).

Outbound actions POST to ``https://graph.facebook.com/<version>/<phone_id>/calls`` per
contracts/whatsapp-calling-webhook.md. Tokens and secrets are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import logging

import httpx

from app.core.config import get_settings

logger = logging.getLogger(__name__)


class MetaCallingError(Exception):
    """Raised when a Meta Graph API call-control action fails (FR-009)."""


def verify_signature(raw_body: bytes, header: str | None) -> bool:
    """Constant-time HMAC-SHA256 check of the ``X-Hub-Signature-256`` header (FR-003)."""

    if not header or not header.startswith("sha256="):
        return False

    settings = get_settings()
    expected = hmac.new(
        settings.whatsapp_app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    provided = header.removeprefix("sha256=")
    return hmac.compare_digest(expected, provided)


async def _call_action(call_id: str, action: str, **extra: object) -> dict:
    settings = get_settings()
    url = (
        f"https://graph.facebook.com/{settings.graph_api_version}"
        f"/{settings.whatsapp_phone_id}/calls"
    )
    body: dict[str, object] = {
        "messaging_product": "whatsapp",
        "call_id": call_id,
        "action": action,
        **extra,
    }
    headers = {"Authorization": f"Bearer {settings.whatsapp_token}"}

    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise MetaCallingError(
                f"Graph call action '{action}' failed for {call_id}: {exc}"
            ) from exc

    if response.is_error:
        # Meta returns a JSON error body (error.message / code / error_subcode) that explains
        # exactly why the action was rejected — surface it instead of a bare status code.
        detail = response.text
        logger.error(
            "Graph call action '%s' rejected for %s: HTTP %s -> %s",
            action,
            call_id,
            response.status_code,
            detail,
        )
        raise MetaCallingError(
            f"Graph call action '{action}' failed for {call_id}: "
            f"HTTP {response.status_code} {detail}"
        )

    return response.json()


async def pre_accept(call_id: str, sdp: str) -> dict:
    """Send early media (pre-accept) with our SDP answer for faster connect."""

    return await _call_action(call_id, "pre_accept", session={"sdp_type": "answer", "sdp": sdp})


async def accept(call_id: str, sdp: str) -> dict:
    """Accept the call, sending our SDP answer to connect the media session."""

    return await _call_action(call_id, "accept", session={"sdp_type": "answer", "sdp": sdp})


async def reject(call_id: str) -> dict:
    """Decline an incoming call."""

    return await _call_action(call_id, "reject")


async def terminate(call_id: str) -> dict:
    """End an active call."""

    return await _call_action(call_id, "terminate")
