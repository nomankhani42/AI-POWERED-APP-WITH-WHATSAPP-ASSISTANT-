# Contract: Room-Type Selection (Tool + Interactive List)

## RunContext extension

`RunContext` (trusted context — never a model argument) gains:

```python
channel: Literal["api", "whatsapp", "voice"] = "api"
```

Set by the caller: REST `/chat` → `"api"`; WhatsApp inbound message turn →
`"whatsapp"`; voice media session → `"voice"`.

## Tool: `offer_room_types`

Read-only agent tool. No model-supplied arguments.

**Behavior by channel** (types always come from `list_active_room_types()` — the
distinct canonical types among active rooms, enum order; never a hardcoded list):

| Channel | Side effect | Return value to the model |
|---------|-------------|---------------------------|
| `whatsapp` | Sends the interactive list (below) to `wrapper.context.phone_number` | Sentinel: options were shown as a tappable list — acknowledge briefly, do NOT re-enumerate |
| `voice` | none | Enumerated types with capacity, phrased for speech (FR-010) |
| `api` | none | Enumerated types with capacity, plain text |
| any, zero active types | none | "no room types are currently available" message |

**Send-failure fallback** (`whatsapp` channel): if the Graph send fails, the tool
returns the enumerated-text form instead, so the conversation still works; the
failure is logged.

## Interactive list payload (verified shape)

Sent via the existing `_post_message` transport; new sender
`send_room_type_list(to, room_types)` in `services/whatsapp_messages.py`.

```json
{ "messaging_product": "whatsapp", "to": "<wa_id>", "type": "interactive",
  "interactive": {
    "type": "list",
    "body": { "text": "Which room type would you like?" },
    "action": {
      "button": "Room types",
      "sections": [ {
        "title": "Available room types",
        "rows": [
          { "id": "room_type:single",  "title": "Single",  "description": "sleeps 1" },
          { "id": "room_type:deluxe",  "title": "Deluxe",  "description": "sleeps 2" }
        ] } ] } } }
```

**Limits honored**: ≤10 rows total (8 canonical types max), button ≤20 chars, row
title ≤24 (capitalized type name), row description ≤72 (short capacity/summary), row
id ≤200 (`room_type:<canonical>`). Rows carry no images (API limit). Freeform send is
valid because it always replies to an inbound guest message (24h window open).

## Tap → turn mapping

`interactive.list_reply.id == "room_type:<canonical>"` → the guest's next agent turn
input is `<canonical>` (see webhook contract). The agent therefore experiences a
typed room-type answer and proceeds with availability/booking (FR-007).

## Prompt contract (`assistant.py`)

- The system prompt MUST NOT enumerate room types or prices (removes the current
  5-vs-8 drift).
- It MUST instruct the agent to call `offer_room_types` whenever the guest needs to
  pick or narrow by room type and hasn't named a valid one.
- Typed input remains first-class: exact/close matches map to canonical types; if
  `check_availability` reports an unknown type, the agent calls `offer_room_types`
  again rather than guessing (FR-008).

## `check_availability` filter normalization

The `room_type` argument is normalized (strip/lower) and validated against
`RoomType`; an unknown value returns a tool message naming the valid types so the
model re-offers options — it never queries with a junk filter.

## Test hooks

- Tool on `whatsapp` channel → Graph payload matches the shape above (mock
  transport), return value is the sentinel.
- Tool on `voice`/`api` → no send; enumerated text listing exactly the active types.
- All rooms of a type deactivated → that type absent from rows/text (spec US3-AS3).
- Graph send failure → enumerated-text fallback + logged error.
- `check_availability(room_type=" Deluxe ")` → normalized to `deluxe`;
  `room_type="penthouse"` → "unknown type" tool message.
