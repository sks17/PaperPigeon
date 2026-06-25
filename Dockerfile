# Repopulation API service image (the new FastAPI + Postgres backend).
# Serves backend.repopulation.api:app — GET /api/graph/data (+ ?run=), /api/node/description,
# /api/lab, /health. The legacy Vercel/Flask + static-cache app is unaffected; this is the
# forward backend for the cutover (AGENTS.md target runtime). Deployed to fly.io (see fly.toml).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8080

WORKDIR /app

# Runtime deps for the engine (SQLAlchemy / pgvector / psycopg / FastAPI / uvicorn / trafilatura).
COPY backend/repopulation/requirements.txt ./engine-requirements.txt
RUN pip install --upgrade pip && pip install -r engine-requirements.txt

# Only what the service + release_command need (keep the image lean; no node_modules / tests data
# beyond the seed cache).
COPY backend ./backend
COPY scripts ./scripts
COPY public/graph_cache.json ./public/graph_cache.json

EXPOSE 8080

# Bind to $PORT (fly sets it) and listen on all interfaces.
CMD ["sh", "-c", "uvicorn backend.repopulation.api:app --host 0.0.0.0 --port ${PORT}"]
