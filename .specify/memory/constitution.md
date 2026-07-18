<!--
Sync Impact Report
==================
Version change: 1.0.0 → 2.0.0
Bump rationale: MAJOR normative technology-stack amendment. Active text-to-speech changes
  from Cartesia Sonic to Deepgram Aura; Cartesia remains an approved rollback dependency.

Modified principles:
  - II. Async-First FastAPI Service — Deepgram covers active STT/TTS I/O; Cartesia is rollback.
  - IV. Voice Pipeline Integrity — fixed active pipeline now ends in Deepgram Aura TTS.
  - V. Configuration & Secrets Discipline — Cartesia credentials are retained only while
    rollback support remains.
  - VI. Documentation-Driven Development — both active and rollback SDKs remain in scope.
  - Technology Stack — normative TTS changed to Deepgram Aura; Cartesia marked rollback-only.

Added specifications:
  - specs/006-deepgram-tts-enhancement/

Templates requiring updates:
  ✅ .specify/templates/plan-template.md — generic provider-neutral gate; no edit required.
  ✅ .specify/templates/spec-template.md — no hardcoded provider references.
  ✅ .specify/templates/tasks-template.md — no hardcoded provider references.
  ✅ .specify/templates/checklist-template.md — no hardcoded provider references.

Follow-up TODOs: None.
-->

# Voice Calling Agent Constitution

## Core Principles

### I. Modular Architecture

Code MUST be organized by concern and feature, never as a single monolithic file. Each
distinct responsibility — API routing, agent orchestration, STT, TTS, memory access,
Meta/WhatsApp integration, configuration — lives in its own module with a clear, single
purpose. A file that mixes transport, business logic, and I/O MUST be split before it is
merged. When a function or file grows past its single responsibility, extract cohesive
helpers or submodules rather than appending more branches.

Rationale: Voice pipelines fan out across many external services; a modular layout keeps
each integration independently testable, replaceable, and reviewable, and prevents the
"everything in `main.py`" failure mode.

### II. Async-First FastAPI Service

The service MUST be built on FastAPI served by Uvicorn, with `uv` as the sole dependency
and environment manager. All I/O-bound work (HTTP calls to Meta, Deepgram, the retained Cartesia rollback,
OpenAI; Redis and MongoDB access) MUST use non-blocking async clients. Blocking calls in
the request/event path are prohibited; offload unavoidable CPU-bound work explicitly.
Dependencies MUST be pinned and reproducible via `uv` lockfiles.

Rationale: Real-time voice demands concurrency without thread starvation; async I/O and a
single reproducible toolchain (`uv`) are non-negotiable for latency and deployability.

### III. Layered Memory

Memory MUST be layered: Redis for short-term cache and in-flight session/turn state,
MongoDB for long-term persistent memory (conversation history, user profiles, durable
records). Ephemeral state MUST NOT be written only to Redis when it must survive a
restart; durable state MUST NOT be read from MongoDB on hot paths without a Redis cache
layer. Each store is accessed through its own dedicated module, never inline in endpoint
or agent code.

Rationale: Separating volatile cache from durable persistence keeps hot-path latency low
while guaranteeing that important conversation data survives process and cache loss.

### IV. Voice Pipeline Integrity

The voice pipeline MUST follow the fixed active contract: audio in → Deepgram (STT) →
OpenAI Agents SDK with the GPT-4.1 model (reasoning/tools) → Deepgram Aura (TTS) →
audio out, integrated with the Meta API for call transport. Cartesia Sonic MAY remain as
an isolated rollback implementation but MUST NOT be selected by the public TTS interface
unless an explicit rollback change is approved. Each stage MUST be an
isolated, individually testable component behind a stable interface so a provider can be
swapped without rewriting the pipeline. Failures in any stage MUST be handled explicitly
(timeout, fallback, or graceful error to the caller) — never silently dropped.

Rationale: A voice call is only as reliable as its weakest stage; explicit stage
boundaries and error handling are what make the end-to-end experience robust.

### V. Configuration & Secrets Discipline

All configuration and credentials (Meta API tokens, Deepgram, retained Cartesia rollback,
OpenAI keys,
Redis and MongoDB URIs) MUST come from environment variables loaded through a single
typed settings module. Secrets MUST NEVER be hardcoded, logged, or committed; `.env`
files MUST be git-ignored. Every required setting MUST fail fast at startup with a clear
error if missing.

Rationale: Multiple third-party services multiply the blast radius of a leaked key;
centralized, validated, env-based config is the minimum safe baseline.

### VI. Documentation-Driven Development (Context7 & Sub-Agents)

Before integrating or upgrading any external SDK or API (Meta, Deepgram, the retained
Cartesia rollback,
OpenAI Agents SDK, Redis, MongoDB, FastAPI), current documentation MUST be consulted via
Context7 rather than relying on memory. Specialized sub-agents and skills MUST be used
when a task matches their domain (e.g., FastAPI backend work, WhatsApp/Meta integration)
instead of ad-hoc implementation.

Rationale: These APIs change quickly; grounding work in fetched docs and routing tasks to
the right specialist agent reduces defects and rework.

## Technology Stack

The following stack is normative and MUST NOT be substituted without a constitution
amendment:

- **Runtime & tooling**: Python with `uv` for dependency and environment management.
- **Web framework**: FastAPI, served by Uvicorn.
- **Agent layer**: OpenAI Agents SDK using the GPT-4.1 model.
- **Speech-to-text**: Deepgram.
- **Text-to-speech**: Deepgram Aura.
- **TTS rollback**: Cartesia Sonic may remain installed and independently tested, but is not
  part of the active calling path.
- **Call transport**: Meta API.
- **Cache / short-term memory**: Redis.
- **Persistent / long-term memory**: MongoDB.

Any additional dependency MUST justify its inclusion and prefer the smallest option that
solves the problem (YAGNI).

## Development Workflow

- Features are developed through the spec → plan → tasks → implement flow; the
  Constitution Check gate in the plan MUST pass before design proceeds.
- Each module MUST be independently importable and testable; integration points to
  external services SHOULD be mockable at their module boundary.
- Environment variables required by a change MUST be documented in the project README/env
  example in the same change.
- External API usage MUST be verified against Context7-fetched documentation before merge.

## Governance

This constitution supersedes ad-hoc conventions. All plans, reviews, and implementations
MUST verify compliance with these principles; deviations MUST be recorded in the plan's
Complexity Tracking table with justification and the rejected simpler alternative.

Amendments require: a written description of the change, an updated version per the policy
below, and propagation to any dependent templates and docs in the same change.

Versioning policy (semantic):
- **MAJOR**: Backward-incompatible governance changes — removing or redefining a
  principle, or changing the normative technology stack.
- **MINOR**: Adding a new principle or section, or materially expanding guidance.
- **PATCH**: Clarifications, wording, and non-semantic refinements.

Compliance is reviewed at each plan gate and at code review. Runtime development guidance
(agent-specific instructions, README) MUST stay consistent with this constitution.

**Version**: 2.0.0 | **Ratified**: 2026-07-03 | **Last Amended**: 2026-07-11
