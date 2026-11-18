# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.11.11 AS uv
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-install-project
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev --no-editable
RUN useradd --system --uid 10001 mock
USER mock
EXPOSE 8081
CMD ["python", "-m", "sap_mock"]
