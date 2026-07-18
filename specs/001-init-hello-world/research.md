# Phase 0 Research: Project Initialization (Hello World)

No `NEEDS CLARIFICATION` items remained after Technical Context — the user and the
constitution fully specify the stack (Python + `uv` + FastAPI + Uvicorn). This document
records the key technology decisions and their rationale.

## Decision 1: Dependency & environment management — `uv`

- **Decision**: Use `uv` with `pyproject.toml` + committed `uv.lock`.
- **Rationale**: Mandated by constitution Principle II. Fast, reproducible installs and a
  single lockfile satisfy SC-004 (reproducible setup) and FR-001 (single documented setup
  step: `uv sync`).
- **Alternatives considered**: pip + requirements.txt (no lockfile guarantees), Poetry
  (slower, not the constitution's chosen tool).

## Decision 2: Web framework & server — FastAPI + Uvicorn

- **Decision**: FastAPI application served by Uvicorn; async route handlers.
- **Rationale**: Mandated by Principle II. FastAPI provides the ASGI async model the voice
  pipeline will need and first-class typed request/response models.
- **Alternatives considered**: Flask (sync-first, not async-native), raw Starlette (less
  ergonomic for typed responses).

## Decision 3: Application composition — app factory + routers

- **Decision**: A `create_app()` factory in `main.py` that includes `APIRouter`s from
  `api/routes/`. Uvicorn entrypoint documented for local run.
- **Rationale**: Satisfies Principle I (Modular Architecture) — routing lives in dedicated
  modules, not inline in the entry file, and the factory makes the app testable with
  `TestClient` without starting a server.
- **Alternatives considered**: Single-file app with inline route decorators (violates
  Principle I, becomes the "everything in main.py" failure mode).

## Decision 4: Configuration — pydantic-settings, fail-fast

- **Decision**: A single `Settings` class (pydantic-settings `BaseSettings`) in
  `core/config.py`, loaded from environment / `.env`, with required fields validated at
  startup. `.env` git-ignored; `.env.example` committed.
- **Rationale**: Satisfies Principle V and FR-004 (fail-fast on missing config) and
  edge-case handling. Establishes the config module future secrets (Meta, Deepgram,
  Deepgram TTS, Cartesia rollback, OpenAI, Redis, MongoDB) will extend.
- **Alternatives considered**: `os.getenv` scattered in code (no validation, no fail-fast,
  violates Principle V).
- **Note**: For this hello-world feature there are no strictly-required secrets yet; the
  `Settings` class defines app-level fields (e.g. `app_name`, `environment`, `port`) with
  sensible defaults and demonstrates the fail-fast pattern that later required secrets use.

## Decision 5: Testing — pytest + FastAPI TestClient

- **Decision**: pytest with a shared `TestClient` fixture in `conftest.py`.
- **Rationale**: Standard, in-process HTTP testing of endpoints without a live server;
  validates acceptance scenarios for the greeting and health stories.
- **Alternatives considered**: httpx live-server integration tests (unnecessary weight for
  a skeleton; reserved for later network-dependent features).

## Documentation grounding (Principle VI)

FastAPI/Uvicorn/pydantic-settings patterns used here were verified against the project's
`fastapi` skill (production FastAPI patterns: async routes, pydantic v2 schemas,
pydantic-settings config, TestClient testing). Context7 documentation lookups are deferred
to the features that introduce new third-party SDKs (Meta, Deepgram, Cartesia rollback, OpenAI
Agents SDK, Redis, MongoDB), per Principle VI.
