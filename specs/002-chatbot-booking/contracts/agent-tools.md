# Agent Tools Contract

The agent (GPT-4.1) may call these function tools. Each is a `@function_tool` in
`src/app/agent/tools.py`. The guest's `phone_number` is NOT a tool argument — tools read it
from trusted run context, so booking actions are always scoped to the caller (FR-008).

All dates are ISO `YYYY-MM-DD`. Tools return concise natural-language-friendly strings (or
structured data the agent summarizes) and never raise raw errors to the model — invalid
input yields a clear message the agent relays.

## check_availability

- **Purpose**: Rooms free for a stay (US1, FR-003).
- **Arguments**: `check_in: date`, `check_out: date`, optional `room_type: str`.
- **Behavior**: Returns rooms with no overlapping active booking for `[check_in, check_out)`.
  Rejects invalid ranges (past `check_in`, `check_out ≤ check_in`).
- **Returns**: list of `{ name, room_type, capacity }` (possibly empty).

## book_room

- **Purpose**: Create a booking (US2, FR-004).
- **Arguments**: `room_name: str`, `check_in: date`, `check_out: date`,
  optional `guest_name: str`.
- **Behavior**: Verifies the room exists and is free for the whole range; creates an active
  booking owned by the context phone number; generates a unique `reference`. Fails clearly if
  the room is unavailable/overlapping (FR-005) or the range is invalid (FR-010).
- **Returns**: `{ reference, room_name, check_in, check_out, status }` or an unavailable
  message.

## cancel_booking

- **Purpose**: Cancel a booking (US3, FR-006).
- **Arguments**: `reference: str`.
- **Behavior**: Cancels the booking only if it belongs to the context phone number and is
  active; frees the room for that range. Not-found / not-owned → clear message, no change
  (FR-008, FR-010). Already-cancelled → reports status (edge case).
- **Returns**: `{ reference, status }` or a not-found/permission message.

## list_bookings

- **Purpose**: List the guest's bookings (US4, FR-007).
- **Arguments**: optional `status: "active" | "cancelled" | "all"` (default `all`).
- **Behavior**: Returns only bookings owned by the context phone number (FR-008/SC-005).
- **Returns**: list of `{ reference, room_name, check_in, check_out, status }` (possibly
  empty).

## Run context

```text
RunContext:
  phone_number: str   # trusted; set by the endpoint from the request, not the model
```

Passed to `Runner.run(..., context=RunContext(phone_number=...))`; every tool scopes its
data access to `context.phone_number`.
