# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim AS frontend-build
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV UV_LINK_MODE=copy

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
COPY scripts/ ./scripts/
RUN uv sync --frozen

COPY --from=frontend-build /app/frontend/dist ./frontend/dist

RUN mkdir -p /app/data /data/Statements
EXPOSE 8000

ENV TH_STATEMENTS_ROOT=/data/Statements
ENV TH_SQLITE_PATH=/app/data/trading.sqlite
ENV TH_DUCKDB_PATH=/app/data/market.duckdb

CMD ["uv", "run", "trade-history", "serve", "--host", "0.0.0.0", "--port", "8000"]

