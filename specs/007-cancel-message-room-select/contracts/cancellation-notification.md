# Contract: Automated Cancellation Notification

## Trigger

The single choke point is the shared tool path — every channel (REST chat, WhatsApp
chat, voice call) cancels through `_cancel_booking_impl`:

```
repo.cancel_booking(reference, phone_number)   # atomic active→cancelled
  ├─ returns Booking  → transition happened HERE → send exactly one notice
  └─ returns None     → no transition (unknown ref / already cancelled /
                         not this guest's booking) → send NOTHING
```

**Atomicity requirement (FR-003)**: `db/bookings.cancel_booking` MUST transition via
an atomic find-and-modify conditioned on `status == active` scoped to the guest's
phone number, so concurrent/repeated cancels yield exactly one Booking return and
therefore exactly one message. This is a data guarantee, not best-effort.

## Message

Sent to `booking.phone_number` as freeform WhatsApp text (existing
`booking_cancellation_text`):

```
Your booking has been cancelled.
Reference: <reference>
Room: <room_name>
Dates: <check_in> to <check_out>
```

Content requirement (FR-002): cancelled statement + reference + room name + both
dates. Wording may evolve; the four elements may not be dropped.

## Failure isolation & observability (FR-004, clarification Q3)

- The send is wrapped (`_safe_send_booking_cancellation`); `WhatsAppMessageError`
  and any other exception are caught — a delivery failure NEVER propagates, blocks,
  or reverts the cancellation.
- Each failure emits exactly one **structured, operator-visible log entry** at ERROR
  level carrying at minimum: event tag (`booking.cancellation_notice_failed`),
  booking reference, recipient number, and the error class/summary. No message
  bodies of unrelated content, no secrets.
- No persisted delivery-status field; no automatic retries.
- The in-conversation reply (agent text / spoken confirmation) still tells the guest
  the cancellation succeeded.

## Known limitation (accepted in plan)

Voice-call cancellation by a guest with no prior inbound WhatsApp text may be outside
the 24-hour customer-service window → Graph rejects the freeform send (e.g. error
131047). Handled as a normal logged delivery failure. Template-message fix is out of
scope for 007.

## Test hooks

- Successful cancel (mock transport) → exactly one Graph send with the four content
  elements.
- Second cancel of the same reference → `None` from repo, zero sends.
- Transport raises / Graph 4xx → cancellation still returns success to the guest;
  one structured ERROR log entry with reference + recipient (assert via `caplog`).
- Cancel via chat flow and via the WhatsApp inbound flow → identical notice behavior
  (same tool path).
