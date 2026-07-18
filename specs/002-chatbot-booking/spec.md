# Feature Specification: Conversational Booking Assistant (Chat + Tools)

**Feature Branch**: `002-chatbot-booking`

**Created**: 2026-07-03

**Status**: Draft

**Input**: User description: "create a chatboat route that answer querries and tools . should book order cancel order like features given below — can answer about available rooms in a resturant / can book rooms for given date / can cancel booking / can show bookings / can show available Reservation +etc"

## Clarifications

### Session 2026-07-03

- Q: Booking domain & availability model — hotel rooms, restaurant tables, or generic? → A: Hotel-style rooms booked by calendar date; availability is a per-date free/taken check (no time slots or party size).
- Q: How is the guest identified for scoping their bookings? → A: A trusted identifier carried with each conversation — the guest's calling/contact phone number (from the voice/WhatsApp channel); the assistant scopes all bookings to it and trusts it (no separate in-conversation login).
- Q: Booking date model — single date or date range? → A: Date range (check-in and check-out) supporting one or more nights; the room must be free for every night in the range and availability is checked against the whole range.
- Q: How long must conversation context persist? → A: Only for the active conversation (short-term); once the conversation ends the chat context is not retained. Booking records remain durable data independently.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Ask About Room Availability (Priority: P1)

A guest sends a chat message asking which rooms are available (optionally for a specific
date) and the assistant replies conversationally with the matching options, so the guest
can decide what to book without browsing a form.

**Why this priority**: Availability lookup is the entry point to every booking and is
read-only, making it the simplest independently testable slice. It proves the
conversational assistant can understand a request and answer using live booking data —
the walking skeleton of the whole feature.

**Independent Test**: Send an availability question through the chat interface and confirm
the reply lists rooms that are actually free for the requested date (and none that are
taken), with no other feature present.

**Acceptance Scenarios**:

1. **Given** rooms exist with some free and some taken for a date, **When** the guest asks
   "what rooms are available on <date>?", **Then** the assistant replies listing only the
   rooms free on that date.
2. **Given** the guest asks about availability without a date, **When** the message is
   received, **Then** the assistant asks for the date (or applies a sensible default) and
   still returns a usable answer.
3. **Given** no rooms are free for the requested date, **When** the guest asks, **Then**
   the assistant clearly states nothing is available for that date.

---

### User Story 2 - Book a Room for a Date (Priority: P2)

A guest asks the assistant to book a specific room for a given date; the assistant
confirms the details, creates the booking, and returns a confirmation with a reference the
guest can refer to later.

**Why this priority**: Booking is the core transactional value of the feature, but it
depends on the guest first being able to find availability (US1).

**Independent Test**: Ask the assistant to book an available room for a date and confirm a
booking is created and a confirmation with a reference is returned; the same room then no
longer appears as available for that date.

**Acceptance Scenarios**:

1. **Given** a room is free for a date, **When** the guest asks to book it for that date,
   **Then** the assistant creates the booking and returns a confirmation with a booking
   reference.
2. **Given** a room is already taken for the requested date, **When** the guest asks to
   book it, **Then** the assistant declines and explains the room is unavailable, offering
   alternatives when possible.
3. **Given** the guest's request is missing required details (e.g., date or room), **When**
   the assistant processes it, **Then** it asks for the missing information before booking.

---

### User Story 3 - Cancel a Booking (Priority: P3)

A guest asks the assistant to cancel an existing booking; the assistant identifies the
booking, cancels it, and confirms, freeing the room for that date.

**Why this priority**: Cancellation is important for a complete experience but is lower
frequency and depends on bookings existing (US2).

**Independent Test**: With an existing booking, ask the assistant to cancel it and confirm
the booking is marked cancelled and the room becomes available again for that date.

**Acceptance Scenarios**:

1. **Given** the guest has an active booking, **When** they ask to cancel it (by reference
   or description), **Then** the assistant cancels it and confirms.
2. **Given** the guest refers to a booking that does not exist or is not theirs, **When**
   they ask to cancel, **Then** the assistant explains it cannot find a matching booking
   and does not cancel anything.

---

### User Story 4 - View My Bookings (Priority: P3)

A guest asks to see their current bookings and the assistant lists them with their key
details (room, date, status, reference).

**Why this priority**: Improves transparency and supports cancellation, but is secondary to
creating and cancelling bookings.

**Independent Test**: With one or more bookings for a guest, ask "show my bookings" and
confirm the assistant lists exactly that guest's bookings with correct details.

**Acceptance Scenarios**:

1. **Given** the guest has bookings, **When** they ask to see them, **Then** the assistant
   lists each booking's room, date, status, and reference.
2. **Given** the guest has no bookings, **When** they ask, **Then** the assistant states
   they have none.

---

### Edge Cases

- What happens when the guest's message is ambiguous or unrelated to bookings? The
  assistant MUST respond helpfully (ask a clarifying question or state what it can help
  with) rather than performing an unintended action.
- How does the system handle two guests trying to book the same room for overlapping stays
  at once? Only one booking MUST succeed; the other MUST be told the room is no longer
  available.
- What happens when the requested stay is invalid (check-in in the past, check-out on or
  before check-in, or malformed dates)? The assistant MUST reject it with a clear
  explanation and not create a booking.
- What happens when the guest asks to cancel a booking that is already cancelled? The
  assistant MUST report the current status without error.
- How are actions scoped so a guest cannot view or cancel another guest's bookings? The
  assistant MUST only act on bookings belonging to the requesting guest.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST accept a conversational message from a guest and return a
  natural-language reply.
- **FR-002**: The assistant MUST retain context across turns within a single active
  conversation so a guest can refer back to previously mentioned rooms, dates, or bookings.
  This context is short-term only and need not persist after the conversation ends; booking
  records are stored durably and independently of conversation context.
- **FR-003**: The assistant MUST answer room-availability questions for a requested stay
  (check-in to check-out), returning only rooms that are free for every night in the range.
- **FR-004**: The assistant MUST create a booking for a specified room and stay dates
  (check-in and check-out, one or more nights) on the guest's request, and return a
  confirmation containing a unique booking reference.
- **FR-005**: The assistant MUST prevent double-booking: a room MUST NOT be bookable for a
  stay that overlaps any night of an existing active booking for that room.
- **FR-006**: The assistant MUST cancel a booking identified by the guest and confirm the
  cancellation, making the room available again for that date.
- **FR-007**: The assistant MUST list the requesting guest's bookings with room, date,
  status, and reference.
- **FR-008**: The assistant MUST identify the guest by the phone number carried with the
  conversation and MUST only allow that guest to view or cancel bookings owned by that
  phone number.
- **FR-009**: When required details are missing (e.g., date or room), the assistant MUST
  ask for them before acting, rather than guessing silently.
- **FR-010**: The assistant MUST reject invalid requests (past/malformed dates,
  non-existent rooms or bookings) with a clear explanation and take no destructive action.
- **FR-011**: The assistant MUST confirm the intended change back to the guest for
  actions that create or cancel a booking.
- **FR-012**: For messages outside its scope, the assistant MUST respond with what it can
  help with instead of failing silently or taking an unintended action.

### Key Entities

- **Room**: A bookable unit that can be reserved for a stay (one or more nights). Key
  attributes: identifier/name, descriptive details (e.g., type/capacity), and its
  availability per night.
- **Booking (Reservation)**: A guest's reservation of a room for a stay. Key attributes:
  unique reference, the room, check-in date, check-out date (one or more nights), the
  owning guest, and a status (active/cancelled).
- **Guest (Customer)**: The person conversing with the assistant, identified by their
  calling/contact phone number so their bookings can be scoped to them. Key attributes:
  phone number (identifier) and display name.
- **Conversation**: The ongoing chat session that carries short-term context across turns
  for a guest during a single active conversation; not retained after it ends.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A guest can find available rooms for a date through conversation in a single
  message exchange, with the answer reflecting current availability 100% of the time.
- **SC-002**: A guest can complete a booking through conversation in under 5 turns (message
  exchanges) starting from an availability question.
- **SC-003**: The system never confirms two active bookings for the same room with
  overlapping stay dates (0 double-bookings across concurrent attempts).
- **SC-004**: A guest can cancel a booking and see it reflected as cancelled/available on
  the next availability or bookings query 100% of the time.
- **SC-005**: A guest can only ever see or cancel their own bookings (0 cross-guest access).
- **SC-006**: For unsupported or ambiguous requests, the assistant returns a helpful reply
  rather than an error or unintended action in at least 95% of such cases.

## Assumptions

- Domain is hotel-style room booking (confirmed in Clarifications): a room is reserved for
  a calendar date and availability is a per-date free/taken check. Restaurant table
  reservations (time slots, party sizes) are explicitly out of scope.
- A booking is made for a stay defined by a check-in and check-out date and may span one or
  more nights (confirmed in Clarifications). Availability requires the room to be free for
  every night in the range. Time-slot reservations remain out of scope.
- The guest is identified by the calling/contact phone number carried with the conversation
  (from the voice/WhatsApp channel), reusing the platform's existing notion of a customer;
  the identifier is trusted and a separate in-conversation login flow is out of scope.
- Room and booking data already exist in (or will be seeded into) the platform's data store;
  this feature consumes and updates that data rather than defining new administration
  screens for it.
- This chat assistant is text-based; connecting it to the voice pipeline (calling, speech
  in/out) is handled by separate features.
- Payments and pricing are out of scope for this feature.
