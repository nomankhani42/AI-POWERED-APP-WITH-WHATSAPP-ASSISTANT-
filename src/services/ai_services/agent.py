"""The Grand Dine — AI-powered restaurant booking assistant for WhatsApp.

Uses the OpenAI Agents SDK with GPT-4o-mini (tracing disabled).
Short-term memory: Upstash Redis per WhatsApp chat (TTL 1 hour).
"""

from __future__ import annotations
import traceback

from agents import (
    Agent,
    ModelSettings,
    RunErrorHandlerInput,
    RunErrorHandlerResult,
    Runner,
    set_default_openai_key,
    set_tracing_disabled,
)

from config import OPENAI_API_KEY
from database import get_db
from crud.customer import upsert_customer
from services.ai_services.context import UserContext
from services.ai_services.upstash_memory import get_session
from services.ai_services.tools import (
    book_room,
    cancel_my_booking,
    check_booking_status,
    get_current_datetime,
    get_hotel_details,
    get_my_bookings,
    get_room_types_info,
    search_available_rooms,
    search_hotels,
)

set_tracing_disabled(True)
set_default_openai_key(OPENAI_API_KEY)

# ---------------------------------------------------------------------------
# OPTIMIZED SYSTEM PROMPT
# Token reduction strategy:
#   - Removed verbose section headers
#   - Collapsed repeated info into tables
#   - Removed obvious instructions ("respond with text")
#   - Kept only rules that change behavior
# ---------------------------------------------------------------------------

AGENT_INSTRUCTIONS = """\
You are The Grand Dine Assistant — WhatsApp concierge for The Grand Dine, F-7 Markaz Islamabad.

First message only: "Welcome to The Grand Dine! 🍽️ I'm your reservation assistant. How can I help?"

## Venue (5 floors)
Ground: Café/Patio | 1st: Family/Private | 2nd: VIP/Executive | 3rd: Banquet | Roof: Sky Lounge

## Rooms & Capacity
Single 2-4 | Double 6-8 | Suite 8-12 | Deluxe 15-20

## Durations
Session ~3h | Half-Day ~6h | Full-Day ~12h | Multi-Day = full-day × days

## Slots
Breakfast 8AM | Morning 10AM | Lunch 12PM | Afternoon 2PM | Evening 6PM | Dinner 7PM

## Rules
- Call get_current_datetime FIRST for any relative date (today/tomorrow/tonight)
- No time given → ask: breakfast/lunch/dinner?
- "dinner/lunch" alone → session; "full day/all day" → full_day
- Max 3 tool calls/message. No duplicate calls.
- Prices PKR. Times PKT.
- Short WhatsApp-friendly replies. Use *bold*, line breaks.

## Booking steps (in order)
Date → Time slot → Type → Guest count → Room (optional) → Summary → Confirm
"""

# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------

grand_dine_agent: Agent[UserContext] = Agent(
    name="The Grand Dine Assistant",
    instructions=AGENT_INSTRUCTIONS,
    model="gpt-4o-mini",
    model_settings=ModelSettings(
        temperature=0.7,
        max_tokens=512,          # Reduced: WhatsApp replies are short
    ),
    tools=[
        get_current_datetime,
        search_hotels,
        get_hotel_details,
        search_available_rooms,
        get_room_types_info,
        book_room,
        check_booking_status,
        cancel_my_booking,
        get_my_bookings,
    ],
)

# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

def _on_max_turns(_data: RunErrorHandlerInput[UserContext]) -> RunErrorHandlerResult:
    return RunErrorHandlerResult(
        final_output="Taking too long — try rephrasing or a simpler question 🙏",
        include_in_history=False,
    )

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def get_agent_response(
    user_message: str,
    whatsapp_number: str,
) -> str:
    """Run agent with Upstash Redis short-term memory.

    Args:
        user_message: Incoming WhatsApp message body.
        whatsapp_number: Sender's phone number.

    Returns:
        Agent's text response.
    """
    db = get_db()
    customer = await upsert_customer(db, whatsapp_number=whatsapp_number)

    user_ctx = UserContext(
        whatsapp_number=whatsapp_number,
        customer_id=customer.customer_id,
    )

    session = get_session(whatsapp_number)

    try:
        result = await Runner.run(
            starting_agent=grand_dine_agent,
            input=user_message,
            context=user_ctx,
            session=session,
            max_turns=10,                # Reduced from 15 → saves tokens on runaway loops
            error_handlers={"max_turns": _on_max_turns},
        )

        output = result.final_output

        if not output or not str(output).strip():
            return "Sorry, couldn't generate a response. Please try again! 🙏"

        return str(output)

    except Exception as e:
        print(f"[Grand Dine] Agent error: {e}")
        traceback.print_exc()
        return "Having a little trouble right now. Please try again in a moment! 🙏"