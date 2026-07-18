# Quickstart: Project Initialization (Hello World)

Validates the walking skeleton end-to-end: reproducible setup, service boot, and the
greeting + health endpoints.

## Prerequisites

- `uv` installed ([astral.sh/uv](https://docs.astral.sh/uv/))
- Python 3.12 (uv can provision it: `uv python install 3.12`)

## Setup (from a clean checkout)

```bash
# Install locked dependencies reproducibly (FR-001, SC-004)
uv sync

# Create local env file from the template (no secrets required for this feature)
cp .env.example .env
```

## Run the service

```bash
uv run uvicorn app.main:app --reload --app-dir src
```

Expected: Uvicorn reports startup and `Application startup complete` with no errors
(User Story 1, Scenario 1).

## Validate the endpoints

In a second terminal:

```bash
# Greeting — expect: {"message":"hello world","success":true}
curl -s http://localhost:8000/

# Health — expect: {"status":"ok","service":"voice-agent"}
curl -s http://localhost:8000/health

# Unknown path — expect HTTP 404 with {"detail":"Not Found"}
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:8000/does-not-exist
```

Response shapes are defined in [contracts/http-api.md](./contracts/http-api.md).

## Validate fail-fast configuration (edge case)

Later features add required settings; the pattern is proven now. To see fail-fast
behavior, temporarily mark a setting required in `src/app/core/config.py`, unset it, and
start the service — it MUST exit at startup with a clear message and MUST NOT start the
server (FR-004).

## Run the tests

```bash
uv run pytest
```

Expected: tests for the greeting and health endpoints pass, asserting the exact response
shapes in the contract.

## Success check

- [ ] `uv sync` completes without manual dependency resolution (SC-004)
- [ ] Service starts and reports ready (US1 S1)
- [ ] `GET /` returns `hello world` with success on first request (US1 S2, SC-002)
- [ ] `GET /health` returns healthy status (US2 S1, SC-003)
- [ ] Unknown path returns 404 (FR-005)
- [ ] Full flow from clean checkout to greeting response takes under 10 minutes (SC-001)
