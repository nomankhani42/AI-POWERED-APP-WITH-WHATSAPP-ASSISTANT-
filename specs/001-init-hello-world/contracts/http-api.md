# HTTP API Contract: Project Initialization (Hello World)

Base URL (local): `http://localhost:8000`

All responses are JSON (`Content-Type: application/json`).

## GET /

Greeting endpoint — the walking-skeleton "hello world" response (FR-002, User Story 1).

**Request**: no parameters, no body, no auth.

**Response `200 OK`**:

```json
{
  "message": "hello world",
  "success": true
}
```

Schema: [GreetingResponse](../data-model.md#greetingresponse)

**Acceptance mapping**:
- US1 Scenario 2 — returns a "hello world" message with a success status.

---

## GET /health

Health/readiness endpoint (FR-003, User Story 2).

**Request**: no parameters, no body, no auth.

**Response `200 OK`**:

```json
{
  "status": "ok",
  "service": "voice-agent"
}
```

Schema: [HealthStatus](../data-model.md#healthstatus)

**Acceptance mapping**:
- US2 Scenario 1 — returns a healthy status while the service is running.

---

## Error behavior

### Unknown path → `404 Not Found` (FR-005)

Any request to an unregistered path returns FastAPI's standard not-found response:

```json
{
  "detail": "Not Found"
}
```

### Startup failures (not HTTP responses)

- Missing required configuration → process exits at startup with a clear error message
  (FR-004). No HTTP server is started.
- Port already in use → Uvicorn exits with a clear bind error identifying the port.

## Notes

- Endpoints are unauthenticated by design for this initialization milestone (see spec
  Assumptions).
- Response field names/values are the stable contract; tests in `tests/` assert against
  exactly these shapes.
