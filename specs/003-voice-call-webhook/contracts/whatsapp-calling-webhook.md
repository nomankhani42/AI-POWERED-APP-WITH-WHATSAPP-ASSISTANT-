# Contract: WhatsApp Business Calling Webhook

Route module: `src/app/api/routes/whatsapp_calling.py`. Registered in `main.py`.
Base URL (local): `http://localhost:8000`. Publicly reachable over HTTPS in production.

## GET /webhooks/whatsapp/calls  — verification handshake

Meta calls this once when the webhook is configured.

**Query params**: `hub.mode=subscribe`, `hub.verify_token=<token>`, `hub.challenge=<int>`.

**Behavior**:
- If `hub.mode == "subscribe"` **and** `hub.verify_token == settings.whatsapp_verify_token`
  → respond `200 OK` with the raw `hub.challenge` value as the body (`text/plain`).
- Otherwise → `403 Forbidden`, empty body, no detail leaked (FR-002, edge: token mismatch).

## POST /webhooks/whatsapp/calls  — call lifecycle events

Meta delivers call events here.

**Headers**: `X-Hub-Signature-256: sha256=<hmac>` — HMAC-SHA256 of the raw body with the app
secret. MUST be validated against `settings.whatsapp_app_secret` before processing (FR-003).

**Body (representative `connect` event)**:

```json
{
  "object": "whatsapp_business_account",
  "entry": [{
    "id": "<waba_id>",
    "changes": [{
      "field": "calls",
      "value": {
        "messaging_product": "whatsapp",
        "metadata": { "display_phone_number": "15550001111", "phone_number_id": "<phone_id>" },
        "calls": [{
          "id": "<call_id>",
          "from": "15557654321",
          "event": "connect",
          "timestamp": "1720180800",
          "session": { "sdp_type": "offer", "sdp": "v=0..." }
        }]
      }
    }]
  }]
}
```

Other `event` values handled: `terminate`, `status` (and unknown types acknowledged, not acted on).

**Behavior**:
1. Read the raw body, verify the signature. Invalid → return `200 OK` (never 4xx/5xx to Meta) but
   process nothing and record nothing (FR-003; skill hard-rule #1).
2. For each call event, dedupe by `event_id` (Redis `SET NX`, durable unique index backstop).
   Duplicate → acknowledge, no side effect (FR-006).
3. Upsert the `Call` record and apply the monotonic state transition; insert a `CallEvent`
   (FR-005). `metadata.display_phone_number` is threaded into call context (multi-tenant safety).
4. For `connect`: hand the SDP offer to the media layer, which answers via
   `meta_calling.accept(call_id, sdp_answer)` and starts `session.run_call_session(...)` — which
   logs the call as attended (FR-015), plays the automatic welcome, and runs the turn loop (see
   [call-session-loop.md](./call-session-loop.md)). Slow work is scheduled so the HTTP response
   still returns within Meta's window (FR-004).
5. For `terminate`: mark the call `ended`, tear down the media session.

**Response**: always `200 OK` with body `{"status": "received"}`. Every exception is caught inside
the handler (FR-004; skill hard-rule #1) so one bad event never fails the batch or triggers Meta
retry floods.

## Meta Graph call actions (outbound, `services/meta_calling.py`)

`POST https://graph.facebook.com/<version>/<phone_id>/calls` via `httpx.AsyncClient`, bearer
`settings.whatsapp_token`:

| Function | `action` | Purpose |
|----------|----------|---------|
| `pre_accept(call_id, sdp)` | `pre_accept` | Early media / faster connect. |
| `accept(call_id, sdp)` | `accept` | Send our SDP answer, connect the call. |
| `reject(call_id)` | `reject` | Decline an incoming call. |
| `terminate(call_id)` | `terminate` | End an active call. |

Failures are raised as typed errors and handled by the caller (FR-009); tokens are never logged.
