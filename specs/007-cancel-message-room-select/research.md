# Research: Cancellation Message Automation & Room Type Selection

**Feature**: 007-cancel-message-room-select | **Date**: 2026-07-15

All Technical Context unknowns resolved. Sources: existing codebase (`src/app`),
meta-whatsapp-api skill references (verified payload shapes), feature-005 agent-turn
contracts, constitution v2.0.0.

## R1. Inbound WhatsApp messages: same webhook, new field dispatch

**Decision**: Reuse the existing `GET/POST /whatsapp/webhook` endpoint. Meta delivers
both call events and messages to the one URL configured on the app; the payload
envelope is identical and the change is distinguished by `value` content —
`value["calls"]` (already handled) vs `value["messages"]` (new). Extend
`_process_payload` in `api/routes/whatsapp_calling.py` to dispatch `messages[]` to a
new `services/whatsapp_chat.py` module. Signature verification, the
always-return-200 rule, and `metadata.display_phone_number` extraction are already
implemented and shared.

**Rationale**: One URL per Meta app is how webhook subscription works; a second
endpoint would never receive traffic. The existing route already implements the
hard rules (always-200, signature check) that the messages path needs.

**Alternatives considered**: Separate `/whatsapp/messages` route — rejected: Meta
posts everything to one configured URL, and duplicating verification logic violates
modularity. Rewriting the route module — rejected: call handling is stable and tested.

## R2. Webhook latency: fire-and-forget agent turns

**Decision**: After signature check and dedupe, process each inbound message as a
background asyncio task via the existing `_fire_and_forget` helper, so the webhook
returns 200 immediately. The task extracts the guest text, runs `run_turn(...)`, and
sends the reply through `services/whatsapp_messages.py`.

**Rationale**: An agent turn takes seconds (LLM + tools); Meta treats slow webhooks
as failures and retries, which would double-process messages. The codebase already
uses this exact pattern for media start on the `connect` call event.

**Alternatives considered**: Inline await (used for call status records, which are
milliseconds) — rejected for turns; a queue/worker — rejected (YAGNI at this volume).

## R3. Message dedupe: Redis-first with Mongo backstop

**Decision**: Dedupe on the inbound message `id` (`wamid…`, globally unique). New
`db/inbound_messages.py` mirroring `db/calls.py`: Redis `SET NX EX` for the hot
check, plus an `InboundMessage` document with a unique index on `wamid` as the
durable backstop. Duplicates are silently dropped.

**Rationale**: Meta retries webhook deliveries; the wamid is the documented
idempotency key (unlike calls, no key synthesis is needed). The two-layer approach
is the constitution's layered-memory pattern and is already proven in `db/calls.py`.

**Alternatives considered**: Redis-only — rejected: a Redis flush during a retry
window reprocesses messages (constitution III forbids Redis-only for
must-survive-restart state). Mongo-only — rejected: adds a DB round-trip to every
webhook hit on the hot path.

## R4. Room-type selection: channel-aware agent tool

**Decision**: Add `channel: Literal["api", "whatsapp", "voice"]` to `RunContext`
(trusted context, never a model argument) and one new read-only tool,
`offer_room_types`. Behavior:

- Fetches the distinct canonical types among active rooms (live catalog, R6).
- `channel == "whatsapp"`: sends an interactive list message to the guest
  (side-effect send, same pattern as `send_booking_confirmation`) and returns a
  sentinel string telling the model the options were already shown as a tappable
  list, so it should not re-enumerate them.
- `channel in ("voice", "api")`: returns the type names (with capacity) for the
  model to speak/write as an enumerated set of choices (FR-010).

The system prompt in `assistant.py` stops hardcoding the eight types with prices and
instead instructs the agent to call `offer_room_types` whenever the guest must pick
a type, and to map close-match typed names onto canonical types (FR-008 retained).

**Rationale**: Tools already carry channel side effects (confirmation/cancellation
sends), so this follows the established pattern; the agent keeps owning conversation
flow, and the voice loop's tool-filler mechanism (feature 005) works unchanged
because it reacts to any `tool_call` event. Removing the hardcoded prose list fixes
the existing drift (prompt listed 5 types while the seed catalog has 8) and satisfies
FR-006 permanently.

**Alternatives considered**: Deterministic pre-agent intercept that pushes a list
before the agent runs — rejected: duplicates intent detection the LLM already does
and breaks mid-conversation flows. Structured "options" field on the chat API
response — rejected per clarification (WhatsApp chat is the selection channel; the
REST chat API gets the enumerated-text fallback for free).

## R5. Interactive list payload and tap handling (verified shapes)

**Decision**: New `send_room_type_list(to, room_types)` in
`services/whatsapp_messages.py` using the existing `_post_message` transport:

```json
{ "messaging_product": "whatsapp", "to": "<wa_id>", "type": "interactive",
  "interactive": {
    "type": "list",
    "body": { "text": "Which room type would you like?" },
    "action": {
      "button": "Room types",
      "sections": [ { "title": "Available room types",
        "rows": [ { "id": "room_type:deluxe", "title": "Deluxe",
                    "description": "sleeps 2" } ] } ] } } }
```

Inbound tap arrives as `type: "interactive"`, `interactive.type: "list_reply"`,
with our payload in `list_reply.id`. `services/whatsapp_chat.py` maps
`room_type:<type>` to the guest message `<type>` and feeds it to `run_turn` — the
agent sees a plain room-type answer and continues normally (FR-007).

**Constraints honored** (meta-whatsapp-api references): ≤10 rows total across
sections (8 types fit one section), button label ≤20 chars, row title ≤24, row
description ≤72, row id ≤200; no images per row. Freeform interactive sends are
valid only inside the 24-hour window — always true here because the list is sent in
reply to an inbound guest message.

**Alternatives considered**: Reply buttons — rejected: max 3 buttons, we have 8
types. WhatsApp Flow — rejected: encryption endpoint + Meta registration for a
single pick is disproportionate (YAGNI).

## R6. Canonical RoomType enum + live options

**Decision**: `RoomType(str, Enum)` in `db/documents.py` with the eight catalog
values: `single, twin, double, deluxe, accessible, family, executive, suite`.
`Room.room_type` becomes `RoomType` with a before-validator that strips/lowercases
incoming strings so casing/spacing variants normalize and anything else is rejected
at write time (FR-009). `db/bookings.py` gains `list_active_room_types()` using a
distinct query over active rooms, ordered by enum declaration order; the
`check_availability` filter normalizes its `room_type` argument the same way and
returns a "not a known type" tool message on failure so the agent re-offers options
(FR-008). `scripts/seed_rooms.py` keys its catalog off the enum so seed data and
schema cannot drift.

**Rationale**: The enum is the single source of truth the spec's clarification
demands; deriving guest-facing options from the DB (not the enum) keeps FR-006's
"only types with active rooms" guarantee.

**Alternatives considered**: Free string + runtime validation table — rejected:
two sources of truth. Admin-managed types collection — rejected: no admin surface
exists; a code-level catalog change is the stated evolution path.

## R7. Cancellation automation hardening

**Decision**: Keep the notification at the tool layer (`_cancel_booking_impl` →
`_safe_send_booking_cancellation`), which all channels share (chat API, WhatsApp
chat, voice — they all run the same agent tools). Harden it to spec:

- **Exactly-once (FR-003)**: `db/bookings.cancel_booking` must perform an atomic
  active→cancelled transition (find-and-modify on `status == active`) and return the
  booking only when the transition happened; retries and unknown references return
  `None` and send nothing. Verify/adjust the current implementation and pin it with
  a test.
- **Failure isolation + observability (FR-004)**: the send wrapper catches
  `WhatsAppMessageError`, never propagates, and emits one structured operator log
  entry per failure — event name, booking reference, recipient, and error class —
  no persisted delivery-status field, no automatic retries (per clarification).
- **Content (FR-002)**: existing `booking_cancellation_text` already carries
  reference, room name, and dates — unchanged.

**Rationale**: The tool layer is the one choke point every cancellation channel
already flows through; atomicity in the repo makes "exactly one message" a data
guarantee rather than a best-effort.

**Known limitation (accepted)**: voice-call cancellations from guests with no prior
WhatsApp text conversation may be outside the 24h window; Meta rejects the freeform
send (error 131047) and it is logged per FR-004. Durable fix = approved template
message — out of scope for 007.

## R8. Testing strategy

**Decision**: Mirror the existing test layout. Contract tests post real envelope
payloads (text, `list_reply`, duplicate wamid, malformed) to the webhook with
signature skip enabled and assert always-200 + dispatch/dedupe. Unit tests cover
`RoomType` normalization/rejection and `list_active_room_types` ordering.
Integration tests run the chat flow with a mocked Graph transport (assert the
interactive list payload and the reply text) and the cancellation path (exactly-once,
failure isolation, structured log record via `caplog`). Tool tests extend
`tests/test_tools.py` for `offer_room_types` per channel using `RunContext`.

**Rationale**: Matches how features 002–006 are tested (fakeredis, TestClient,
transport mocks); every FR maps to at least one test.
