"""Shared context object passed to every agent tool via RunContextWrapper.

The ``UserContext`` carries per-request state (the customer's WhatsApp
number) so tools can look up bookings, conversations, etc. without
requiring the caller to thread it through every argument.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class UserContext:
    """Per-request context injected into the agent run.

    Attributes:
        whatsapp_number: The customer's WhatsApp number in international
            format (no ``+`` prefix).  Set by the webhook handler before
            launching the agent.
        customer_id: Resolved after the first DB lookup.  May remain
            ``None`` for brand-new customers until upsert runs.
        channel: Source channel — ``whatsapp``, ``app``, or ``web``.
        full_name: Customer's display name (pre-filled from app context).
        email: Customer's email (pre-filled from app context).
    """

    whatsapp_number: str
    customer_id: str | None = None
    channel: str = "whatsapp"
    full_name: str = ""
    email: str = ""
