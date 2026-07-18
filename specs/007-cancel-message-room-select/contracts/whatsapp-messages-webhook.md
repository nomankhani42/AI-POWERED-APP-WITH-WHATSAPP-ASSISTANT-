# Contract: WhatsApp Inbound Messages on the Shared Webhook

**Endpoint**: `POST /whatsapp/webhook` (existing endpoint — this contract covers the
new `messages` dispatch; call events keep their 003 contract).

## Invariants (inherited, unchanged)

1. The handler ALWAYS returns `200 {"status": "received"}` — malformed payloads,
   processing errors, and rejected signatures included.
2. `X-Hub-Signature-256` is verified before any processing unless
   `WHATSAPP_SKIP_SIGNATURE=true` (local debug only).
3. `metadata.display_phone_number` is read from the payload, never from env.

## Dispatch

For each `entry[].changes[].value`:

| Payload content | Handler |
|-----------------|---------|
| `value["calls"]` | existing call-event path (feature 003) |
| `value["messages"]` | **NEW** → `services/whatsapp_chat.py`, one background task per message after dedupe |
| `value["statuses"]` | ignored (delivery receipts — out of scope) |

## Accepted inbound message shapes

### Text

```json
{ "from": "9231XXXXXXXXX", "id": "wamid.HBg...", "timestamp": "1752566400",
  "type": "text", "text": { "body": "do you have a family room friday?" } }
```

→ guest message = `text.body`.

### Interactive list reply (room-type tap)

```json
{ "from": "9231XXXXXXXXX", "id": "wamid.HBg...", "timestamp": "1752566400",
  "type": "interactive",
  "interactive": { "type": "list_reply",
    "list_reply": { "id": "room_type:deluxe", "title": "Deluxe",
                    "description": "sleeps 2" } } }
```

→ if `list_reply.id` matches `room_type:<canonical>` the guest message is
`<canonical>`; otherwise fall back to `list_reply.title` as plain text.

### Anything else (`image`, `audio`, `reaction`, unknown `type`)

→ recorded for dedupe, then answered with a brief text reply stating the assistant
handles text only; never an error to Meta.

## Processing pipeline (per message)

```
signature ok → for each messages[i]:
  wamid dedupe (Redis SET NX → Mongo unique-index backstop)   [duplicate → drop]
  → fire-and-forget background task:
      extract guest text (rules above)
      run_turn(text, phone_number=from, conversation_id=from)   [channel="whatsapp"]
      send reply → send_text(...)  (tools may already have sent an interactive list)
  → webhook has already returned 200 (does not wait for the turn)
```

## Failure behavior

| Failure | Behavior |
|---------|----------|
| Signature invalid | log warning, return 200, process nothing |
| Duplicate wamid | drop silently (no reply, no second turn) |
| Agent turn raises | caught in the background task; error logged; optional brief apology text to guest; webhook already 200 |
| Reply send rejected by Graph | logged via the message service's structured error log; no retry |

## Test hooks (contract tests)

- Text envelope → 200 + one turn dispatched with the sender's number.
- Same envelope twice → 200 + exactly one turn.
- `list_reply` with `room_type:deluxe` → turn receives `deluxe`.
- Malformed JSON / missing fields / unknown type → 200, no crash.
- Envelope containing BOTH `calls` and `messages` → both paths dispatched.
