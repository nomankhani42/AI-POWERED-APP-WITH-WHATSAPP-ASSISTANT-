"""Trusted per-request run context for the booking agent.

`phone_number` is set by the endpoint from the request, never by the model, so tools always
scope to the calling guest (FR-008 / SC-005). `channel` (007) tells channel-aware tools how
to present choices: WhatsApp chat gets tappable selection lists, voice and the REST chat API
get enumerated text.
"""

from dataclasses import dataclass
from typing import Literal

Channel = Literal["api", "whatsapp", "voice"]


@dataclass
class RunContext:
    phone_number: str
    channel: Channel = "api"
    # The business's customer-facing WhatsApp number, from the inbound webhook's
    # metadata.display_phone_number (never an env var — multi-tenant safety). Used to
    # build wa.me tap-back links on carousel cards; None outside the whatsapp channel.
    business_number: str | None = None
