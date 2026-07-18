# Phase 1 Data Model: Conversational Booking Assistant

Durable entities live in MongoDB (Beanie documents). Conversation context is short-term in
Redis and is intentionally NOT modeled as a durable entity.

## Room (MongoDB collection: `rooms`)

| Field         | Type      | Description                                | Rules                          |
|---------------|-----------|--------------------------------------------|--------------------------------|
| `id`          | ObjectId  | Unique room identifier                     | Primary key                    |
| `name`        | string    | Human-readable room name/number            | Required, unique               |
| `room_type`   | string    | e.g. single/double/suite                   | Required                       |
| `capacity`    | int       | Max guests                                 | ≥ 1                            |
| `description` | string    | Optional details                           | Optional                       |
| `is_active`   | bool      | Whether the room is bookable at all        | Default `true`                 |

- **Relationships**: referenced by `Booking.room_id`.
- **Availability** is derived (not stored): a room is available for a stay when no active
  booking for it overlaps the requested `[check_in, check_out)`.

## Booking (MongoDB collection: `bookings`)

| Field         | Type      | Description                                | Rules                                        |
|---------------|-----------|--------------------------------------------|----------------------------------------------|
| `id`          | ObjectId  | Internal id                                | Primary key                                  |
| `reference`   | string    | Guest-facing booking reference             | Required, unique, generated                  |
| `room_id`     | ObjectId  | Booked room                                | Required, references `Room`                  |
| `phone_number`| string    | Owning guest (trusted identity)            | Required; all scoping keys on this           |
| `guest_name`  | string    | Display name if known                      | Optional                                     |
| `check_in`    | date      | First night of stay                        | Required; not in the past                    |
| `check_out`   | date      | Departure date (exclusive)                 | Required; strictly after `check_in`          |
| `status`      | enum      | `active` \| `cancelled`                    | Default `active`                             |
| `created_at`  | datetime  | Creation timestamp                         | Set on create                                |
| `cancelled_at`| datetime  | When cancelled                             | Set on cancel; null while active             |

- **Relationships**: `room_id → Room`; `phone_number → Guest` (guest is identity-only).
- **State transitions**: `active → cancelled` (via cancel). Cancelling an already-cancelled
  booking is a no-op that reports current status.
- **Uniqueness / integrity**: `reference` unique. No two `active` bookings for the same
  `room_id` may have overlapping `[check_in, check_out)` intervals.
- **Indexes**: `reference` (unique), `phone_number` (list/scope queries), `room_id`
  (availability/overlap queries).

## Guest (identity only — not a stored document for this feature)

- Represented by `phone_number` carried on the request and stored on each `Booking`.
- `guest_name` optionally captured on a booking. No separate `guests` collection is required
  for this feature (can be added later without breaking bookings).

## Conversation context (Redis — short-term, not durable)

- **Key**: derived from `conversation_id` (defaults to the guest's `phone_number`).
- **Value**: the ordered list of Agents SDK conversation items (managed by `RedisSession`).
- **TTL**: `SESSION_TTL_SECONDS` — context expires after inactivity; not retained long-term.
- Managed exclusively through the `RedisSession(SessionABC)` implementation; never read
  inline elsewhere (Principle III).

## Validation rules sourced from requirements

- Reject `check_in` in the past and `check_out ≤ check_in` (FR-010, edge cases).
- A booking may only be listed/cancelled by its owning `phone_number` (FR-008, SC-005).
- Booking creation must fail if it would overlap an existing active booking (FR-005, SC-003).
