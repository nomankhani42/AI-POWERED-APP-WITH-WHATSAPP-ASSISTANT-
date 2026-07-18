# Phase 1 Data Model: Meta Voice Call Webhook & Speech Services

Durable entities are Beanie `Document`s added to `src/app/db/documents.py` and registered in
`db/mongo.py`'s `init_beanie(...)`. In-flight, volatile state lives in Redis (not modeled as
documents). Follows the existing `Room`/`Booking` conventions (UTC `datetime`, string enums,
indexed lookup fields).

## Entity: Call (durable — MongoDB)

Represents one voice interaction with a caller, from first event to termination.

| Field | Type | Notes |
|-------|------|-------|
| `call_id` | `str` | Meta's unique call identifier. **Unique index** (`uq_call_id`). |
| `wa_call_from` | `str` | Caller's WhatsApp number (identity). Indexed (`ix_call_from`). |
| `display_phone_number` | `str` | Business number from webhook `metadata` (multi-tenant safety, skill hard-rule #4). |
| `status` | `CallStatus` enum | `ringing` \| `connecting` \| `connected` \| `ended` \| `failed`. |
| `conversation_id` | `str` | Links to the `RedisSession` / agent thread (defaults to `wa_call_from`). |
| `started_at` | `datetime` | Set on first lifecycle event. Default `utcnow`. |
| `connected_at` | `datetime \| None` | Set when media is established. |
| `ended_at` | `datetime \| None` | Set on terminate/failure. |
| `end_reason` | `str \| None` | e.g. `caller_hangup`, `agent_terminated`, `error`. |
| `created_at` | `datetime` | Default `utcnow`. |

**State transitions** (FR-005):
`ringing → connecting → connected → ended`; any state `→ failed` on unrecoverable error.
Transitions are monotonic — a later/duplicate event never regresses a call already `ended`/`failed`.

**Validation / rules**:
- `call_id` unique — enforces one record per Meta call (supports idempotency, FR-006).
- Out-of-order `terminate` before `connect` (edge case) reconciles by creating the record in
  `ended` state rather than orphaning the event.

## Entity: CallEvent (durable — MongoDB)

One notification Meta sent about a call; the audit trail + durable idempotency backstop.

| Field | Type | Notes |
|-------|------|-------|
| `event_id` | `str` | Meta delivery/event id. **Unique index** (`uq_call_event_id`) → durable dedupe. |
| `call_id` | `str` | The call this refers to. Indexed (`ix_event_call`). |
| `event_type` | `str` | `connect` \| `terminate` \| `status` \| ... (as delivered). |
| `payload` | `dict` | Raw (sanitized) event body for audit/replay. No secrets/signatures stored. |
| `received_at` | `datetime` | Default `utcnow`. |

**Rules**: insert guarded by the unique `event_id`; a duplicate delivery is acknowledged (200)
without a second insert or repeated side effect (FR-006). Fast-path dedupe uses a Redis
`SET NX` on `call:event:<event_id>` with TTL; this document is the durable backstop.

## Ephemeral state (Redis — not a document)

| Key | Purpose | TTL |
|-----|---------|-----|
| `call:event:<event_id>` | Idempotency marker for in-flight dedupe (FR-006). | short (minutes) |
| `agent:session:<conversation_id>` | Existing `RedisSession` conversation history (reused). | `session_ttl_seconds` |
| Per-call media/turn state | Held in memory within the call's `media/session.py` task, keyed by `call_id`; Redis used only for cross-worker coordination if scaled. | lifetime of call |

## Transient pipeline objects (not persisted)

These flow through the pipeline in memory; they are interface shapes, not stored documents
(matching spec entities "Transcript Segment" and "Speech Response"):

- **TranscriptSegment**: `{ call_id, text, is_final: bool, ts }` — emitted by `stt.transcribe_stream()`
  as Deepgram returns interim/finalized chunks. Final segments feed `run_turn`.
- **SpeechChunk**: `{ call_id, audio: bytes, sample_rate }` — emitted by `tts.synthesize_stream()`
  as the active Deepgram Aura stream returns raw audio bytes; forwarded to the outbound media track. Cartesia can produce the same shape only through the rollback function.
- **ConversationTurn**: `{ call_id, turn: int, transcript: str, reply: str, started_at, ended_at }`
  — one exchange in the call loop, tracked in memory by `media/session.py` and written to the
  structured log for observability (FR-023). `turn = 0` is the automatic welcome (empty
  `transcript`, `reply = welcome_message`); subsequent turns pair the caller transcript with the
  agent reply. Not a stored document.

**Call-attended log (FR-015)**: when a call reaches `connected` (`connected_at` set), the session
emits a structured log event — `call_attended` with `call_id` and `wa_call_from` (caller number).
This is a log record, not a persisted entity; it complements the durable `Call.connected_at` field.

Persisting full transcripts/audio is out of scope for this feature (durable conversation content
already lives in the agent's session/records); only `Call` and `CallEvent` are stored.
