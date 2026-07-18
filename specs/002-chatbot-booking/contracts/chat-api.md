# HTTP API Contract: Chat Endpoint

Base URL (local): `http://localhost:8000`. JSON in/out.

## POST /chat

Send one guest message; receive the assistant's reply. The assistant may call tools to read
or change bookings before replying.

**Request body**:

```json
{
  "message": "Do you have any rooms free from 2026-08-10 to 2026-08-12?",
  "phone_number": "+15551234567",
  "conversation_id": "optional-existing-id"
}
```

| Field             | Type   | Required | Notes                                                            |
|-------------------|--------|----------|------------------------------------------------------------------|
| `message`         | string | yes      | The guest's natural-language message. Non-empty.                 |
| `phone_number`    | string | yes      | Trusted guest identity; scopes all booking actions (FR-008).     |
| `conversation_id` | string | no       | Threads short-term context. Defaults to a value derived from `phone_number`. |

**Response `200 OK`**:

```json
{
  "reply": "Yes — Room 12 (double) is available for those nights. Want me to book it?",
  "conversation_id": "+15551234567"
}
```

| Field             | Type   | Notes                                                    |
|-------------------|--------|----------------------------------------------------------|
| `reply`           | string | Natural-language assistant response.                     |
| `conversation_id` | string | Echoes the id used so the client can continue the thread.|

**Errors**:

- `422 Unprocessable Entity` — missing/invalid `message` or `phone_number` (validation).
- `500` — upstream model/store failure; the reply path must surface a user-friendly message
  where possible rather than leaking internals.

**Notes**:

- The endpoint is async; it awaits the agent run.
- `phone_number` is never passed to the model as a tool argument — it is supplied to tools
  via trusted run context so a guest cannot act on another guest's bookings.
