# One image for the API (which also serves the built website, D-114), the worker and migrations;
# `docker compose` picks the command. Build context: the repository root.

# --- website: src/frontend → static files ---
FROM node:22-alpine AS web
WORKDIR /web
COPY src/frontend/package.json src/frontend/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci --no-audit --no-fund
COPY src/frontend/ ./
RUN npm run build

# --- backend ---
FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.22 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH

WORKDIR /app
# dependencies first (torch CPU wheels are large; this layer survives code changes)
COPY src/backend/pyproject.toml src/backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY src/backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY --from=web /web/dist /app/web

RUN useradd --create-home --uid 10001 kompas
USER kompas
ENV KOMPAS_KNOWLEDGE_DIR=/app/knowledge_data KOMPAS_WEB_DIR=/app/web
EXPOSE 8000
ENTRYPOINT ["kompas"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8000"]
