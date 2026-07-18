# Implementation Plan: Project Initialization (Hello World)

**Branch**: `001-init-hello-world` | **Date**: 2026-07-03 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/001-init-hello-world/spec.md`

## Summary

Initialize the voice calling agent repository as a runnable walking skeleton: a FastAPI
service served by Uvicorn, set up reproducibly with `uv`, that exposes a `hello world`
greeting endpoint and a health endpoint. The structure is modular from the start
(separate app entry, routing, and typed configuration modules) so later features — Meta
calling, Deepgram STT/TTS, OpenAI Agents SDK, Cartesia Sonic rollback, Redis, MongoDB — drop into
their own modules without reorganizing the project.

## Technical Context

**Language/Version**: Python 3.12 (managed by `uv`)

**Primary Dependencies**: FastAPI, Uvicorn (ASGI server), pydantic-settings (typed config)

**Storage**: N/A for this feature (Redis/MongoDB introduced in later features)

**Testing**: pytest with FastAPI `TestClient`

**Target Platform**: Linux server (local developer machine for this initialization feature)

**Project Type**: Web service (single project)

**Performance Goals**: Not performance-sensitive for this feature; endpoints respond
effectively instantly under local development load.

**Constraints**: Async-first (non-blocking route handlers); fail-fast startup on missing
required configuration; no secrets committed (`.env` git-ignored).

**Scale/Scope**: Two endpoints (greeting + health); foundational skeleton only.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Gate | Status |
|-----------|------|--------|
| I. Modular Architecture | App entry, routing, and config in separate modules; no monolithic file | PASS — structure below separates `main`, `api/`, `core/config` |
| II. Async-First FastAPI Service | FastAPI + Uvicorn, `uv` managed, async handlers, pinned/locked deps | PASS — stack matches; `uv.lock` committed |
| III. Layered Memory | Redis + MongoDB layering | N/A — no memory in this feature; deferred to later features |
| IV. Voice Pipeline Integrity | STT→Agent→TTS pipeline contract | N/A — no voice pipeline in this feature |
| V. Configuration & Secrets Discipline | Env-based typed settings, fail-fast, `.env` ignored | PASS — `core/config.py` via pydantic-settings; `.env.example` documented |
| VI. Documentation-Driven Development | Context7 consulted before SDK integration; use sub-agents/skills | PASS — FastAPI/Uvicorn patterns are the only integration; verified against the `fastapi` skill; Context7 to be used when SDKs are added |

**Result**: PASS. No violations. Principles III and IV are not applicable to an
initialization feature and are intentionally deferred; this is not a deviation requiring
Complexity Tracking.

## Project Structure

### Documentation (this feature)

```text
specs/001-init-hello-world/
├── plan.md              # This file (/speckit-plan command output)
├── research.md          # Phase 0 output (/speckit-plan command)
├── data-model.md        # Phase 1 output (/speckit-plan command)
├── quickstart.md        # Phase 1 output (/speckit-plan command)
├── contracts/           # Phase 1 output (/speckit-plan command)
│   └── http-api.md
└── tasks.md             # Phase 2 output (/speckit-tasks command - NOT created by /speckit-plan)
```

### Source Code (repository root)

```text
pyproject.toml           # uv project metadata + dependencies
uv.lock                  # reproducible locked dependencies
.env.example             # documented required settings (no secrets)
.gitignore               # ignores .env, __pycache__, .venv, etc.
README.md                # setup & run instructions

src/
└── app/
    ├── __init__.py
    ├── main.py          # FastAPI app factory + Uvicorn entrypoint; wires routers
    ├── core/
    │   ├── __init__.py
    │   └── config.py    # pydantic-settings Settings; fail-fast on missing config
    └── api/
        ├── __init__.py
        └── routes/
            ├── __init__.py
            ├── greeting.py   # GET / → hello world
            └── health.py     # GET /health → status

tests/
├── __init__.py
├── conftest.py          # TestClient fixture
├── test_greeting.py
└── test_health.py
```

**Structure Decision**: Single-project web service. The `src/app` package separates the
application entry (`main.py`), configuration (`core/config.py`), and routing
(`api/routes/*`) into distinct modules to satisfy Principle I. Each future integration
(memory, STT, TTS, agent, Meta calling) will be added as a sibling package under
`src/app/` (e.g. `src/app/services/…`, `src/app/memory/…`) without touching this skeleton.

## Complexity Tracking

> No constitution violations. Table intentionally empty.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| _(none)_  | —          | —                                   |
