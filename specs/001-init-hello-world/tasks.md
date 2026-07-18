---
description: "Task list for Project Initialization (Hello World)"
---

# Tasks: Project Initialization (Hello World)

**Input**: Design documents from `/specs/001-init-hello-world/`

**Prerequisites**: plan.md (required), spec.md (required), research.md, data-model.md, contracts/http-api.md, quickstart.md

**Tests**: Included — the plan and quickstart both specify pytest + FastAPI `TestClient` coverage for the endpoints.

**Organization**: Tasks are grouped by user story to enable independent implementation and testing.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

- Single-project web service: source under `src/app/`, tests under `tests/` at repository root (per plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Reproducible `uv` project and modular skeleton so any story can start.

- [X] T001 Initialize the `uv` project at repo root: create `pyproject.toml` (name, Python 3.12 requirement) via `uv init`, then add runtime deps `fastapi`, `uvicorn[standard]`, `pydantic-settings` with `uv add`
- [X] T002 Add dev dependency `pytest` and `httpx` with `uv add --dev pytest httpx`, and generate the committed `uv.lock` via `uv sync`
- [X] T003 [P] Create the modular package skeleton (empty `__init__.py` files): `src/app/__init__.py`, `src/app/core/__init__.py`, `src/app/api/__init__.py`, `src/app/api/routes/__init__.py`, `tests/__init__.py`
- [X] T004 [P] Create `.gitignore` at repo root ignoring `.env`, `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`

**Checkpoint**: `uv sync` succeeds; package directories exist and are importable.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Config module and app factory that every endpoint depends on.

**⚠️ CRITICAL**: No user story endpoint can be wired until this phase is complete.

- [X] T005 Implement typed settings in `src/app/core/config.py`: a pydantic-settings `Settings` class with fields `app_name` (default `voice-agent`), `environment` (default `development`), `host` (default `0.0.0.0`), `port` (default `8000`), reading from environment/`.env`, plus a cached `get_settings()` accessor (per data-model.md; fail-fast pattern per Principle V / FR-004)
- [X] T006 [P] Create `.env.example` at repo root documenting the settings from T005 with default values and no secrets (FR-007)
- [X] T007 Implement the FastAPI app factory `create_app()` and module-level `app` in `src/app/main.py`: instantiate FastAPI (title from settings) and include the greeting and health routers; keep routing out of the entry logic (Principle I) — routers are added in their story phases

**Checkpoint**: `uv run python -c "from app.main import app"` (with `--app-dir src`) imports without error.

---

## Phase 3: User Story 1 - Runnable Service Returns Hello World (Priority: P1) 🎯 MVP

**Goal**: Service boots and `GET /` returns `{"message":"hello world","success":true}`.

**Independent Test**: Start the service and request `/`; a successful hello-world response confirms the story with no other feature present.

### Tests for User Story 1

> Write these tests FIRST; ensure they FAIL before implementation.

- [X] T008 [P] [US1] Add `tests/conftest.py` with a `client` fixture returning a FastAPI `TestClient` built from `app.main.create_app()`
- [X] T009 [P] [US1] Contract test in `tests/test_greeting.py`: `GET /` returns 200 with body exactly `{"message":"hello world","success":true}` (per contracts/http-api.md)

### Implementation for User Story 1

- [X] T010 [P] [US1] Define `GreetingResponse` pydantic response model (`message: str`, `success: bool`) in `src/app/api/routes/greeting.py`
- [X] T011 [US1] Implement `GET /` async handler in `src/app/api/routes/greeting.py` on an `APIRouter`, returning `GreetingResponse(message="hello world", success=True)`
- [X] T012 [US1] Ensure the greeting router is included by `create_app()` in `src/app/main.py` (wire-up from T007)

**Checkpoint**: `uv run pytest tests/test_greeting.py` passes; `GET /` returns hello world. MVP deliverable.

---

## Phase 4: User Story 2 - Health/Readiness Check (Priority: P2)

**Goal**: `GET /health` returns `{"status":"ok","service":"<app_name>"}`.

**Independent Test**: With the service running, request `/health` and confirm a healthy status.

### Tests for User Story 2

- [X] T013 [P] [US2] Contract test in `tests/test_health.py`: `GET /health` returns 200 with `status == "ok"` and `service` equal to the configured `app_name` (per contracts/http-api.md)

### Implementation for User Story 2

- [X] T014 [P] [US2] Define `HealthStatus` pydantic response model (`status: str`, `service: str`) in `src/app/api/routes/health.py`
- [X] T015 [US2] Implement `GET /health` async handler in `src/app/api/routes/health.py` on an `APIRouter`, sourcing `service` from `get_settings().app_name` and returning `status="ok"`
- [X] T016 [US2] Ensure the health router is included by `create_app()` in `src/app/main.py`

**Checkpoint**: `uv run pytest tests/test_health.py` passes; both US1 and US2 endpoints work independently.

---

## Phase 5: User Story 3 - Modular Project Skeleton Ready for Features (Priority: P3)

**Goal**: A clean checkout sets up reproducibly with one documented step, and the layout separates concerns for future features.

**Independent Test**: From a clean checkout, run the documented setup step and confirm reproducible install; review the layout to confirm entry/routing/config are separate modules.

- [X] T017 [P] [US3] Write `README.md` at repo root: project overview and setup/run instructions (`uv sync`, `cp .env.example .env`, `uv run uvicorn app.main:app --reload --app-dir src`) plus the endpoint list (FR-007, SC-001)
- [X] T018 [US3] Verify reproducible setup from clean state: run `uv sync` and confirm `uv.lock` fully resolves with no manual steps (SC-004); fix `pyproject.toml`/lock if needed
- [X] T019 [US3] Verify modular separation matches plan.md structure — `main.py` (entry/factory), `api/routes/*` (routing), `core/config.py` (config) are distinct with no route logic in `main.py` (FR-006, Principle I)

**Checkpoint**: All three stories independently functional; project is a clean modular skeleton for future features.

---

## Phase 6: Polish & Cross-Cutting Concerns

**Purpose**: Verify edge cases and the full walking-skeleton flow.

- [X] T020 [P] Add edge-case test in `tests/test_greeting.py` (or `tests/test_errors.py`): unknown path `GET /does-not-exist` returns 404 with `{"detail":"Not Found"}` (FR-005)
- [X] T021 Run the full `quickstart.md` validation end-to-end (setup → run → curl `/`, `/health`, unknown path → `uv run pytest`) and confirm every success check passes (SC-001..SC-004)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — start immediately.
- **Foundational (Phase 2)**: Depends on Setup — BLOCKS all user stories.
- **User Stories (Phase 3–5)**: All depend on Foundational. US1, US2, US3 are independent of each other and can proceed in parallel once Phase 2 is done.
- **Polish (Phase 6)**: Depends on the endpoints from US1/US2 existing.

### Story Independence

- **US1 (P1)**: Greeting endpoint — independent; MVP.
- **US2 (P2)**: Health endpoint — independent; separate route file.
- **US3 (P3)**: Docs + reproducibility + structure verification — independent (touches README and verifies, no endpoint code).

### Within Each User Story

- Tests written first and failing → response models → handlers → router wire-up.

## Parallel Opportunities

- Setup: T003 and T004 in parallel.
- Foundational: T006 in parallel with T005 (different files); T007 after T005.
- US1: T008, T009, T010 in parallel (distinct files); T011 after T010; T012 after T011.
- US2: T013, T014 in parallel; T015 after T014; T016 after T015.
- US3: T017 in parallel; T018/T019 are verification.
- Cross-story: once Phase 2 done, US1, US2, and US3 can be developed in parallel by different people.

### Parallel Example: User Story 1

```bash
# Launch US1 independent tasks together:
Task: "tests/conftest.py TestClient fixture"          # T008
Task: "tests/test_greeting.py contract test"          # T009
Task: "GreetingResponse model in src/app/api/routes/greeting.py"  # T010
```

## Implementation Strategy

### MVP First (User Story 1 only)

1. Complete Phase 1 (Setup) and Phase 2 (Foundational).
2. Complete Phase 3 (US1) → `GET /` returns hello world.
3. **STOP and VALIDATE**: run `uv run pytest tests/test_greeting.py` and `curl /`. This is the demonstrable MVP.

### Incremental Delivery

1. Setup + Foundational → skeleton ready.
2. US1 → hello world (MVP) → demo.
3. US2 → health check → demo.
4. US3 → README + reproducibility + structure verification.
5. Polish → edge cases + full quickstart validation.

## Notes

- [P] = different files, no dependency on an incomplete task.
- Every story is independently testable; stop at any checkpoint to validate.
- Verify tests fail before implementing (T009, T013).
- Commit after each task or logical group.
