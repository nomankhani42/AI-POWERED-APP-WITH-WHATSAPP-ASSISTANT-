FROM python:3.12-slim

# Install system deps (ffmpeg for pydub audio processing)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies (cached layer)
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./src/
COPY scripts/ ./scripts/

ENV PYTHONUNBUFFERED=1

# Koyeb injects PORT env var (default 8000 for local dev)
ENV PORT=8000
EXPOSE ${PORT}

CMD uvicorn main:app --host 0.0.0.0 --port ${PORT} --app-dir src
