# syntax=docker/dockerfile:1.7
# Imagen del backend (api, scheduler, worker y migraciones). Contexto de build: raíz del repo.
#   docker build -f deploy/docker/backend.Dockerfile --target runtime -t sapextract-backend .

FROM ghcr.io/astral-sh/uv:0.11.11 AS uv

# ---- sistema: ODBC Driver 18 para los destinos SQL Server (plan 07 §3)
FROM python:3.14-slim AS os-base
# packages-microsoft-prod.deb instala la clave y el repositorio de la versión de Debian.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && . /etc/os-release \
    && curl -fsSL -o /tmp/ms-prod.deb \
       "https://packages.microsoft.com/config/debian/${VERSION_ID}/packages-microsoft-prod.deb" \
    && dpkg -i /tmp/ms-prod.deb && rm /tmp/ms-prod.deb \
    && apt-get update \
    && ACCEPT_EULA=Y apt-get install -y --no-install-recommends msodbcsql18 unixodbc \
    && apt-get purge -y curl && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

FROM os-base AS base
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
CMD ["uvicorn", "sapextract.main_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--reload", "--reload-dir", "/app/src"]

# ---- build de producción
FROM deps AS build
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable

# ---- runtime mínimo, sin root
FROM os-base AS runtime
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
# Cabeceras X-Forwarded-*: las aplica la API solo para SX_TRUSTED_PROXIES (plan 13, C-07 d).
CMD ["uvicorn", "sapextract.main_api:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
