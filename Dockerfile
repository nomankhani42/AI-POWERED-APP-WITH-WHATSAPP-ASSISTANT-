FROM python:3.12-slim

# Faster, cleaner logs and no .pyc clutter.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Bring in the uv binary (small, no pip install needed).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

WORKDIR /app

# Copy lock + metadata first so deps cache until they change.
COPY pyproject.toml uv.lock README.md ./

# Install locked production deps (project itself added after code copy).
RUN uv sync --locked --no-install-project --no-dev

# Then the app code.
COPY . .

# Install the project itself now that src/ is present.
RUN uv sync --locked --no-dev

EXPOSE 8000

# uv run uses the project venv; --no-sync skips a redundant dependency check.
CMD ["uv", "run", "--no-sync", "uvicorn", "src.app.main:app", "--host", "0.0.0.0", "--port", "8000"]