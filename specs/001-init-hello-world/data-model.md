# Phase 1 Data Model: Project Initialization (Hello World)

This feature has no persistent data store. The only "entities" are the transient
response shapes returned by the two endpoints and the application settings object. They
are documented here as the contract for response schemas.

## GreetingResponse

The payload returned by the greeting endpoint.

| Field     | Type    | Description                                  | Rules                          |
|-----------|---------|----------------------------------------------|--------------------------------|
| `message` | string  | Human-readable greeting text                 | Non-empty; value `hello world` |
| `success` | boolean | Indicates the request succeeded              | `true` on success              |

- **Relationships**: none.
- **State transitions**: none (stateless response).

## HealthStatus

The payload returned by the health/readiness endpoint.

| Field     | Type   | Description                                  | Rules                            |
|-----------|--------|----------------------------------------------|----------------------------------|
| `status`  | string | Service liveness indicator                   | One of: `ok` (healthy)           |
| `service` | string | Service name, from configuration             | Non-empty                        |

- **Relationships**: `service` is sourced from `Settings.app_name`.
- **State transitions**: none (reflects current liveness at request time).

## Settings (application configuration)

Loaded once at startup from environment / `.env`; not part of any HTTP response body.

| Field         | Type   | Default        | Description                                     |
|---------------|--------|----------------|-------------------------------------------------|
| `app_name`    | string | `voice-agent`  | Service name used in responses/logging          |
| `environment` | string | `development`  | Deployment environment label                    |
| `host`        | string | `0.0.0.0`      | Bind host for Uvicorn                           |
| `port`        | int    | `8000`         | Bind port for Uvicorn                           |

- **Validation**: Type-validated by pydantic-settings at startup. Any field marked
  required in a future feature MUST cause fail-fast startup if absent (Principle V, FR-004).
- **Source precedence**: process environment overrides `.env`, which overrides defaults.
