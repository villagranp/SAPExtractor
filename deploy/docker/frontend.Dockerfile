# syntax=docker/dockerfile:1.7
# SPA compilada servida por Nginx sin root. Contexto de build: raíz del repo.
#   docker build -f deploy/docker/frontend.Dockerfile -t sapextract-frontend .

FROM node:24-alpine AS build
RUN corepack enable
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/root/.local/share/pnpm/store pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM nginxinc/nginx-unprivileged:1.29-alpine AS runtime
ENV API_UPSTREAM=http://api:8000
COPY deploy/nginx/frontend.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 8080
