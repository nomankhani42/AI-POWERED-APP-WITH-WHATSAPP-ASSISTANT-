# Feature Specification: Cancellation Message Automation & Room Type Selection

**Feature Branch**: `007-cancel-message-room-select`

**Created**: 2026-07-15

**Status**: Implemented

**Input**: User description: "create also message automation for booking cancel and and room type should in select type"

## Clarifications

### Session 2026-07-15

Recorded as recommended defaults (user unavailable during the clarify session); revise
via `/speckit-clarify` if any answer is wrong.

- Q: Where does the guest's tappable room-type selection live, given no inbound WhatsApp
  text webhook exists yet? → A: WhatsApp chat is a guest channel for this feature — the
  assistant sends an interactive selection list over WhatsApp and receiving/processing
  guest text messages and tap replies is in scope.
- Q: Is the room-type set a fixed canonical enum or fully dynamic from room data? → A:
  Fixed canonical set — the eight types in the existing catalog (single, twin, double,
  deluxe, accessible, family, executive, suite) — validated at write time; the
  guest-facing options are the distinct canonical types among currently active rooms.
- Q: How are cancellation-message delivery failures recorded for operators? → A: As a
  structured, operator-visible log entry per failure; no persisted delivery-status field
  on the booking and no automatic retries.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Automated Cancellation Message on Every Cancellation (Priority: P1)

A guest cancels a booking — through the chat assistant or during a voice call — and,
without anyone having to remember to notify them, automatically receives a WhatsApp
message confirming the cancellation with the booking reference, room, and stay dates.
This mirrors the existing booking-confirmation message automation, extending the same
guarantee to cancellations.

**Why this priority**: A cancellation without a written trace leaves guests unsure
whether it actually happened, which produces repeat contacts and disputes. The written
notice is the guest's proof of cancellation, and it is the half of the request that
delivers standalone value even if nothing else ships.

**Independent Test**: Cancel an active booking through each available channel (chat and
voice call) and confirm the guest's WhatsApp number receives exactly one cancellation
message containing the reference, room name, and dates — with no other part of this
feature present.

**Acceptance Scenarios**:

1. **Given** a guest with an active booking, **When** they cancel it via the chat
   assistant, **Then** the cancellation succeeds and the guest's WhatsApp number
   automatically receives one message confirming the cancellation with the booking
   reference, room name, and check-in/check-out dates.
2. **Given** a guest cancels their booking during a voice call, **When** the
   cancellation completes, **Then** the same written cancellation message is delivered
   to their WhatsApp number even though the interaction was spoken.
3. **Given** a cancellation attempt fails (unknown reference, already cancelled, or not
   the guest's booking), **When** the attempt is rejected, **Then** no cancellation
   message is sent.
4. **Given** the messaging service is unreachable when a cancellation succeeds,
   **When** the notification cannot be delivered, **Then** the booking remains
   cancelled, the delivery failure is recorded for operators, and the assistant still
   tells the guest in-conversation that the cancellation went through.

---

### User Story 2 - Choose Room Type from a Selection List (Priority: P2)

While checking availability or booking over WhatsApp chat, a guest who needs to specify
a room type is shown the available room types as a tappable selection list instead of
having to type one. They tap an option and the conversation continues with that choice
applied.

**Why this priority**: Free-typed room types ("delux", "family room?", "the big one")
force clarification round-trips and mis-matches. A selection list removes guesswork,
but it depends on the guest-facing conversation working at all, so it ranks below the
cancellation guarantee.

**Independent Test**: Ask the assistant over WhatsApp chat about rooms without naming a
type; confirm a selection list of the current room types is offered, that tapping an
option continues the availability/booking flow with that type, and that typing a type
name still works.

**Acceptance Scenarios**:

1. **Given** a guest asks about availability without specifying a room type, **When**
   the assistant needs the type to proceed (or offers to narrow results), **Then** the
   guest is presented the available room types as a selectable option list rather than
   being asked to type one.
2. **Given** the guest taps a room-type option, **When** the selection is received,
   **Then** the assistant treats it as the guest's room-type answer and continues the
   flow (availability check or booking) using that type.
3. **Given** the guest ignores the selection list and types a room type instead,
   **When** the text matches or closely matches a known type, **Then** it is accepted
   and mapped to that type.
4. **Given** the guest types something that matches no known room type, **When** the
   assistant cannot map it, **Then** it re-offers the selectable room-type options
   instead of failing or guessing.
5. **Given** the guest selects a room type with no availability for their dates,
   **When** the check runs, **Then** the assistant clearly says that type has nothing
   available and offers the other types.

---

### User Story 3 - Room Types Are a Governed Set (Priority: P3)

Room records carry a room type drawn from a defined set of types rather than free text,
and the selection options shown to guests always reflect the types that actually exist
in the active room catalog — so the choices guests see never drift out of sync with the
rooms that can be booked.

**Why this priority**: The selection experience in Story 2 is only trustworthy if the
underlying data cannot contain misspelled or ad-hoc type values. This is the data
discipline that keeps the feature honest over time, but it delivers no guest-visible
value on its own.

**Independent Test**: Attempt to create or seed a room with a type outside the defined
set and confirm it is rejected; add a room of an existing type and confirm the
guest-facing selection options continue to match the distinct types of active rooms.

**Acceptance Scenarios**:

1. **Given** the defined set of room types, **When** a room is created or updated with
   a type outside that set (including casing/spacing variants), **Then** the value is
   rejected or normalized to the canonical type — free-form variants never persist.
2. **Given** the active room catalog, **When** the assistant offers room-type options,
   **Then** the options are exactly the distinct types of currently active rooms — not
   a separately maintained prose list.
3. **Given** all rooms of a type are deactivated, **When** a guest is next offered
   room-type options, **Then** that type no longer appears.

---

### Edge Cases

- A cancellation is retried (duplicate request for the same reference): only the first
  successful cancellation sends a message; the retry is rejected and sends nothing.
- The guest cancels during a voice call while their phone is in use: the WhatsApp
  message queues normally and is readable after the call — spoken confirmation on the
  call is not replaced by the message.
- Notification delivery fails permanently: the failure is recorded once for operators;
  the system does not retry indefinitely or block other work.
- The number of room types exceeds what a single selection prompt can display: the
  options are presented in a way that keeps every type reachable (e.g., grouped or
  paginated presentation), never silently truncated.
- The guest is on a voice call (no tappable interface): the assistant speaks the room
  types as an enumerated set of choices; the selection-list requirement applies only to
  the messaging channel.
- A guest taps a room-type option long after it was offered (stale conversation): the
  selection is still interpreted as a room-type answer in a fresh context rather than
  causing an error.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: On every successful booking cancellation, regardless of the channel used
  to cancel (chat or voice call), the system MUST automatically send a cancellation
  message to the WhatsApp number associated with the booking.
- **FR-002**: The cancellation message MUST state that the booking is cancelled and
  include the booking reference, room name, and check-in/check-out dates.
- **FR-003**: Exactly one cancellation message MUST be sent per successful
  cancellation; rejected or failed cancellation attempts MUST NOT trigger a message.
- **FR-004**: A failure to deliver the cancellation message MUST NOT block or revert
  the cancellation itself; each delivery failure MUST be recorded as a structured,
  operator-visible log entry (no automatic retries, no persisted delivery-status field),
  and the guest MUST still receive in-conversation confirmation.
- **FR-005**: When the assistant needs the guest to specify a room type during a
  WhatsApp chat conversation, it MUST present the available room types as a selectable
  option list the guest can tap, rather than requiring typed input.
- **FR-005a**: The system MUST receive and process guest messages sent over WhatsApp
  chat — both plain text and selection-list tap replies — so the WhatsApp conversation
  is a fully working assistant channel for this feature.
- **FR-006**: The room-type options offered to guests MUST be derived from the distinct
  types of currently active rooms in the catalog, so the options always match what can
  actually be booked.
- **FR-007**: A guest's tapped selection MUST be accepted as their room-type answer and
  drive the subsequent availability check or booking without re-asking.
- **FR-008**: Typed room-type input MUST remain accepted; input that matches or closely
  matches a known type is mapped to it, and unrecognized input causes the assistant to
  re-offer the selectable options.
- **FR-009**: Room type on room records MUST be constrained to the fixed canonical set
  of room types (single, twin, double, deluxe, accessible, family, executive, suite);
  values outside the set (including casing or spacing variants) MUST be rejected or
  normalized at write time.
  Changing the canonical set is a deliberate catalog change, not a data-entry side
  effect.
- **FR-010**: On voice calls, the assistant MUST offer the same room types as a spoken,
  enumerated set of choices, since no tappable interface exists on that channel.

### Extension (2026-07-16): Room photo carousel

- **FR-011**: Each room record MAY carry a photo (a publicly reachable image); the seeded
  catalog MUST provide one verified photo per room type.
- **FR-012**: When a guest on WhatsApp chat asks for available rooms and at least two of
  the matching rooms have photos, the assistant MUST present them as a swipeable card
  carousel (photo, room name/type/capacity, and a "Book" button that pre-fills a booking
  message back into the chat). With fewer than two photographed matches, or if the
  carousel cannot be delivered, the assistant falls back to the plain text listing.
- **FR-013**: The carousel MUST never block or break the availability answer — carousel
  delivery failures are logged and the guest still receives the room list as text.

### Key Entities

- **Booking**: A guest's reservation (reference, room, guest phone number, check-in and
  check-out dates, status). Cancellation of a booking is the trigger for the automated
  cancellation message.
- **Cancellation Message**: The automated written notice sent to the booking's phone
  number after a successful cancellation; carries reference, room name, and dates.
  Delivery failures are observable by operators through structured log entries.
- **Room**: A bookable room with a name and a room type drawn from the canonical set.
- **Room Type**: A member of the fixed canonical set (single, twin, double, deluxe,
  accessible, family, executive, suite), enforced at write time. The distinct canonical
  types among active rooms form the selection options shown to guests.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of successful cancellations result in exactly one cancellation
  message when the messaging service is reachable, across every cancellation channel.
- **SC-002**: Zero cancellations are blocked, delayed, or reverted because a
  notification could not be delivered.
- **SC-003**: The cancellation message arrives within 1 minute of the cancellation
  completing under normal service conditions.
- **SC-004**: A guest can specify a room type with a single tap; at least 90% of
  room-type selections proceed without a correction round-trip.
- **SC-005**: After rollout, zero room records exist with a type outside the defined
  set, and the guest-facing options never list a type with no active rooms.

## Assumptions

- WhatsApp is the guest messaging channel: the phone number stored on the booking is
  both the guest's identity and the destination for automated messages, consistent with
  the existing booking-confirmation automation.
- "Room type should be in select type" is interpreted as two complementary rules: guests
  choose a room type from a presented selection list (not free typing), and room-type
  values in room data are constrained to a defined set rather than free text.
- The canonical set of room types is the existing catalog's eight types (single, twin,
  double, deluxe, accessible, family, executive, suite); evolving the set is a
  deliberate catalog change, and guest-facing options are always the distinct canonical
  types among active rooms rather than hardcoded prose.
- A basic cancellation notice already exists on the chat-tool cancellation path; this
  feature turns it into a guaranteed automation across all cancellation paths with
  defined content, single-send behavior, and recorded delivery failures.
- WhatsApp chat is a first-class assistant channel for this feature: sending interactive
  selection lists and receiving guest text and tap replies are both in scope (per
  clarification), even though today's backend only exposes a direct chat interface and
  voice calls. Voice calls fall back to spoken enumeration.
- Guests have an active WhatsApp account on the booking's phone number; undeliverable
  numbers surface as recorded delivery failures, not feature errors.
