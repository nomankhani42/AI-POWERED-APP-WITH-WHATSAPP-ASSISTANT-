# Data Model: Cancellation Message Automation & Room Type Selection

**Feature**: 007-cancel-message-room-select | **Date**: 2026-07-15

All documents live in MongoDB via Beanie (`src/app/db/documents.py`); short-term keys
live in Redis. Only deltas from the current model are described — unchanged fields are
listed for context without commentary.

## RoomType (new enum — canonical set)

String enum, single source of truth for the catalog (FR-009, clarification Q2).

| Value | Notes |
|-------|-------|
| `single` | Declaration order is the presentation order everywhere |
| `twin` | |
| `double` | |
| `deluxe` | |
| `accessible` | |
| `family` | |
| `executive` | |
| `suite` | |

**Validation rules**:
- Incoming values are normalized before validation: `strip()` + `lower()` (so
  `" Deluxe "` → `deluxe`).
- Anything not in the set after normalization is rejected at write time with a clear
  error; nothing is silently coerced to a different type.
- Evolving the set is a code change to the enum (deliberate catalog change), which
  automatically propagates to seed data, validation, and guest-facing options.

## Room (modified)

| Field | Type | Change |
|-------|------|--------|
| `name` | `str`, unique index | unchanged |
| `room_type` | **`RoomType`** (was free `str`) | constrained + normalized (FR-009) |
| `capacity` | `int` | unchanged |
| `description` | `str \| None` | unchanged |
| `is_active` | `bool` | unchanged — gates guest-facing options (FR-006) |

**Derived read**: `list_active_room_types() -> list[RoomType]` — distinct
`room_type` values among documents with `is_active == True`, ordered by enum
declaration order. This list (never the raw enum) is what guests are offered.

**Migration note**: existing seeded rooms already use the eight canonical lowercase
strings, so re-validating against the enum requires no data rewrite; `seed_rooms.py`
switches to iterating the enum so the two cannot drift.

## Booking (unchanged shape; behavior pinned)

| Field | Type | Notes |
|-------|------|-------|
| `reference` | `str`, unique | carried in the cancellation message (FR-002) |
| `room_id` | ObjectId ref → Room | |
| `room_name` | `str` | carried in the cancellation message |
| `phone_number` | `str` | destination for automated messages (assumption) |
| `guest_name` | `str \| None` | |
| `check_in` / `check_out` | `date` | carried in the cancellation message |
| `status` | `active \| cancelled` | see state transitions |
| `created_at` / `cancelled_at` | `datetime` | |

**State transition (the notification trigger)**:

```
active ──(atomic find-and-modify, scoped to guest phone)──▶ cancelled
```

- The transition MUST be atomic on `status == active`; only the request that performs
  the transition receives the booking back, and only that request sends the
  cancellation message (FR-001, FR-003 exactly-once).
- Repeat cancels, unknown references, and other guests' references do not transition
  and MUST NOT send anything.
- **Deliberately absent** (clarification Q3): no `notice_sent` / delivery-status
  field — delivery failures are structured log entries, not persisted state.

## InboundMessage (new — webhook dedupe backstop)

Mirrors the `db/calls.py` event-dedupe pattern (constitution III: Redis hot path,
Mongo durable backstop).

| Field | Type | Notes |
|-------|------|-------|
| `wamid` | `str`, **unique index** | Meta's globally unique inbound message id |
| `sender` | `str` | guest wa_id (digits, no `+`) |
| `message_type` | `str` | `text` \| `interactive` (list_reply) \| other |
| `received_at` | `datetime` | UTC, insertion time |

**Lifecycle**: insert-only. A duplicate insert (unique-index violation) or a Redis
`SET NX` miss means the message was already processed → drop silently.

**Redis companion key**: `wamid:<id>` with TTL (session-scale, e.g. 24h) — checked
before Mongo on the hot path.

## Cancellation Message (not persisted — behavioral entity)

Composed at send time from the transitioned Booking; content contract in
[contracts/cancellation-notification.md](./contracts/cancellation-notification.md).
Delivery outcome surfaces only as: success (silent) or one structured operator log
entry per failure (event, reference, recipient, error class).

## Relationships

```
RoomType (enum) ──constrains── Room.room_type
Room (is_active) ──derives──▶ guest-facing room-type options (tool + WhatsApp list)
Booking ──atomic cancel──▶ Cancellation Message → guest phone_number
InboundMessage ──dedupes──▶ agent turn (run_turn) per unique wamid
```
