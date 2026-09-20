# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 — build the React/Vite frontend.
# The API is served from the SAME origin in production, so we build with an
# empty VITE_API_BASE_URL to make the client issue relative "/api/*" calls.
# ---------------------------------------------------------------------------
FROM node:20-alpine AS frontend
WORKDIR /app/frontend

# Install deps first (better layer caching).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Build the static bundle.
COPY frontend/ ./
ENV VITE_API_BASE_URL=""
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — Python backend that also serves the built frontend.
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Backend dependencies.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Backend source + demo seed.
COPY backend/ ./backend/

# Built frontend from stage 1 -> the location create_app() looks for by default
# (<repo>/frontend/dist) so no extra env is needed.
COPY --from=frontend /app/frontend/dist ./frontend/dist

# Where the SQLite DB lives (ephemeral on Render's free tier — reseeded at boot).
ENV MEMBER_TRACKER_DB_PATH=/app/backend/member_tracker.db \
    MEMBER_TRACKER_DEMO_SEARCH=1 \
    PORT=8000

WORKDIR /app/backend

# Seed the demo data at container start (idempotent — wipes + reseeds), then
# launch uvicorn on the platform-provided $PORT. Render sets $PORT; default 8000.
CMD ["sh", "-c", "python seed_demo.py || true; exec python -m uvicorn member_tracker.api.app:app --host 0.0.0.0 --port ${PORT:-8000}"]
