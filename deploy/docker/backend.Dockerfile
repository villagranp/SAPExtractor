# syntax=docker/dockerfile:1.7
# Imagen del backend (api, scheduler, worker y migraciones). Contexto de build: raíz del repo.
#   docker build -f deploy/docker/backend.Dockerfile --target runtime -t sapextract-backend .

FROM ghcr.io/astral-sh/uv:0.11.11 AS uv

FROM python:3.14-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# ---- dependencias (capa cacheable: solo cambia con uv.lock)
FROM base AS deps
COPY backend/pyproject.toml backend/uv.lock backend/README.md ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project

# ---- desarrollo: incluye herramientas dev; el código se monta como volumen
FROM deps AS dev
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-install-project
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked
EXPOSE 8000
CMD ["uvicorn", "sapextract.main_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--reload", "--reload-dir", "/app/src"]

# ---- build de producción
FROM deps AS build
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# ---- runtime mínimo, sin root
FROM python:3.14-slim AS runtime
ARG BUILD_COMMIT=local
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH=/opt/venv/bin:$PATH \
    SX_BUILD_COMMIT=$BUILD_COMMIT \
    SX_SPOOL_DIR=/app/spool
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin sx \
    && mkdir -p /app/spool /app/logs && chown sx:sx /app/spool /app/logs
COPY --from=build /opt/venv /opt/venv
WORKDIR /app
USER sx
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/live', timeout=4).status == 200 else 1)"]
CMD ["uvicorn", "sapextract.main_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
