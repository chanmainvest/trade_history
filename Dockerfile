FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md AGENTS.md ./
COPY spec ./spec
COPY doc ./doc
COPY src ./src

RUN uv sync --locked --no-dev

RUN mkdir -p data logs Statements

EXPOSE 8000
CMD ["ledger", "serve", "--host", "0.0.0.0", "--port", "8000"]
