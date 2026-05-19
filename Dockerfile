# Stage 1: build React frontend
FROM node:22-slim AS frontend-builder

WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Stage 2: Python backend
FROM python:3.12-slim

# Install system deps for pdfplumber (poppler)
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv

WORKDIR /app

# Copy project files
COPY pyproject.toml ./
COPY src/ ./src/

# Install Python dependencies
RUN uv sync --no-dev

# Copy built frontend into FastAPI static dir
COPY --from=frontend-builder /app/frontend/dist/ ./src/trade_history/api/static/

# Runtime data directories
RUN mkdir -p /app/data /app/Statements

ENV DB_PATH=/app/data
ENV STATEMENTS_DIR=/app/Statements

EXPOSE 8000

CMD ["uv", "run", "uvicorn", "trade_history.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
