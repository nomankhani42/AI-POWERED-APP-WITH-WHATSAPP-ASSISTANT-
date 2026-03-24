# Support Agent

FastAPI-based support agent with WhatsApp Meta Cloud API integration.

## Tech Stack

- Python 3.12+
- FastAPI + Uvicorn
- Pydantic v2
- OpenAI Agents SDK
- MongoDB (via `motor` async driver)
- httpx (async HTTP client for Meta API calls)
- Package manager: uv

## Project Structure

```
src/
├── main.py              # FastAPI app entry point (lifespan, router registration)
├── config.py            # Loads .env, exposes settings as module-level constants
├── database.py          # Motor client, connect/close/get_db helpers
├── models/              # Pydantic request/response models
│   ├── __init__.py
│   └── whatsapp.py      # WhatsApp message, request, and response models
├── services/            # Business logic / external API wrappers
│   ├── __init__.py
│   └── whatsapp.py      # Meta WhatsApp Cloud API service (send, upload, templates)
└── endpoints/           # API route modules
    ├── __init__.py
    ├── users.py          # Users router (in-memory demo)
    └── whatsapp.py       # WhatsApp router (webhook, send, templates, messages)
```

## Setup

```bash
uv sync
```

## Run

```bash
cd src && uvicorn main:app --reload
```

## Environment Variables

Stored in `.env` (not committed). Required:

```
MONGO_URI=mongodb://localhost:27017
MONGO_DB_NAME=support_agent
WHATSAPP_VERIFY_TOKEN=<user-chosen-secret>
WHATSAPP_ACCESS_TOKEN=<from-meta-developer-portal>
WHATSAPP_PHONE_NUMBER_ID=<from-meta-business-settings>
WHATSAPP_BUSINESS_ACCOUNT_ID=<from-meta-business-settings>
```

## Conventions

- Use `APIRouter` in `src/endpoints/` for new route modules and register them in `main.py` via `app.include_router()`.
- Define Pydantic models in `src/models/` (one file per resource) and import them in endpoints.
- Place external API wrapper logic in `src/services/` (one file per integration).
- Environment variables are loaded in `src/config.py` via `python-dotenv` and exposed as module-level constants. Import from `config` — do not use `os.getenv()` elsewhere.
- MongoDB must be running locally (default `mongodb://localhost:27017`). Configure via `MONGO_URI` and `MONGO_DB_NAME` in `.env`.
- Database connection is managed via the lifespan context manager in `main.py` using helpers from `database.py`. Use `get_db()` in endpoints/services to access the database.
- All WhatsApp messages (incoming and outgoing) are persisted in the `messages` MongoDB collection.

## WhatsApp Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/whatsapp/webhook` | Meta webhook verification |
| POST | `/whatsapp/webhook` | Receive incoming messages |
| POST | `/whatsapp/send` | Send text message |
| POST | `/whatsapp/send-template` | Send template message |
| POST | `/whatsapp/send-media` | Send media message |
| POST | `/whatsapp/upload-media` | Upload media file to Meta |
| GET | `/whatsapp/templates` | List message templates |
| POST | `/whatsapp/templates` | Create a template |
| DELETE | `/whatsapp/templates/{name}` | Delete a template |
| GET | `/whatsapp/messages` | List stored messages (filterable) |
| GET | `/whatsapp/messages/{id}` | Get single message by ID |
