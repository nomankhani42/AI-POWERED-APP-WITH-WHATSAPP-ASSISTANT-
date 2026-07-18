# Quickstart: Validating 007 — Cancellation Messages & Room-Type Selection

Runnable checks proving the feature end-to-end. Details live in
[data-model.md](./data-model.md) and [contracts/](./contracts/) — this guide only
exercises them.

## Prerequisites

- `.env` with the existing settings (see `.env.example`): `OPENAI_API_KEY`,
  `MONGODB_URI`, `REDIS_URL`, `WHATSAPP_TOKEN`, `WHATSAPP_PHONE_ID`,
  `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`. No new variables in 007.
- MongoDB + Redis reachable (`backend/docker-compose.yml` provides both).
- For local webhook curls only: `WHATSAPP_SKIP_SIGNATURE=true`.

```bash
uv sync
uv run python scripts/seed_rooms.py --reset   # seeds the 8 canonical types via the enum
uv run uvicorn app.main:app --port 8000       # from src/, per project run convention
```

## 1. Automated test suite (primary gate)

```bash
uv run pytest tests/contract/test_messages_webhook.py \
              tests/unit/test_room_type.py \
              tests/integration/test_whatsapp_chat_flow.py \
              tests/integration/test_cancellation_notice.py \
              tests/test_tools.py -q
```

Expected: all pass. Coverage mapping — webhook contract → FR-005a; room-type enum →
FR-009; chat flow → FR-005/006/007/008; cancellation → FR-001..004; tools per
channel → FR-010.

## 2. Inbound text → agent reply (webhook path, US2)

```bash
curl -sX POST localhost:8000/whatsapp/webhook -H 'Content-Type: application/json' -d '{
  "object": "whatsapp_business_account",
  "entry": [{ "changes": [{ "field": "messages", "value": {
    "metadata": { "display_phone_number": "15551234567" },
    "messages": [{ "from": "923001234567", "id": "wamid.QS1",
      "timestamp": "1752566400", "type": "text",
      "text": { "body": "I want to book a room this friday" } }] } }] }] }'
```

Expected: immediate `{"status":"received"}`; logs show one agent turn for
`923001234567`; an outbound Graph send is attempted (interactive room-type list if
the agent asked for a type — inspect the logged payload or use a mock/test number).
Re-sending the exact same curl (same `wamid.QS1`) processes **nothing** (dedupe).

## 3. Tap simulation → list_reply becomes the answer (US2)

```bash
curl -sX POST localhost:8000/whatsapp/webhook -H 'Content-Type: application/json' -d '{
  "object": "whatsapp_business_account",
  "entry": [{ "changes": [{ "field": "messages", "value": {
    "metadata": { "display_phone_number": "15551234567" },
    "messages": [{ "from": "923001234567", "id": "wamid.QS2",
      "timestamp": "1752566460", "type": "interactive",
      "interactive": { "type": "list_reply",
        "list_reply": { "id": "room_type:deluxe", "title": "Deluxe" } } }] } }] }] }'
```

Expected: the turn runs with guest input `deluxe`; the assistant continues the
availability/booking flow for deluxe rooms — no re-asking for the type.

## 4. Cancellation notice, exactly once (US1)

```bash
# Book, then cancel, through the REST chat API (same tool path as all channels)
curl -sX POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"book a deluxe room for 2026-07-20 to 2026-07-22, yes confirm",
       "phone_number":"923001234567"}'
curl -sX POST localhost:8000/chat -H 'Content-Type: application/json' \
  -d '{"message":"cancel my booking <REFERENCE>, yes I am sure",
       "phone_number":"923001234567"}'
```

Expected: cancel reply confirms; logs show exactly one cancellation-notice send with
reference, room, and dates. Repeating the cancel message → "not found / already
cancelled" reply and **zero** additional sends.

## 5. Failure isolation (US1-AS4)

With an invalid `WHATSAPP_TOKEN` (or transport mocked to fail), repeat step 4:
cancellation still succeeds in-conversation; exactly one structured ERROR log entry
`booking.cancellation_notice_failed` with the reference and recipient; booking is
`cancelled` in MongoDB.

## 6. Write-time room-type enforcement (US3)

```bash
uv run python - <<'PY'
import asyncio
from app.db.mongo import init_db, close_db
from app.db.documents import Room

async def main():
    await init_db()
    try:
        await Room(name="Room 999", room_type="Penthouse ", capacity=2).insert()
        print("BUG: junk type accepted")
    except Exception as e:
        print("rejected as expected:", type(e).__name__)
    r = Room(name="Room 998", room_type=" Deluxe ", capacity=2)  # normalizes
    print("normalized:", r.room_type)
    await close_db()

asyncio.run(main())
PY
```

Expected: `Penthouse` rejected; `" Deluxe "` normalized to `deluxe`. Deactivate all
rooms of one type and re-run step 2's flow: that type no longer appears among the
offered options.
