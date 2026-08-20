# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:0.12.5 AS uv

FROM python:3.14-slim

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_NO_PROGRESS=1 \
    UV_PYTHON=3.14 \
    UV_PYTHON_DOWNLOADS=never \
    TOP_ARENA_SERVER_HOST=0.0.0.0 \
    TOP_ARENA_SERVER_PORT=8910

RUN apt-get update \
    && apt-get install --yes --no-install-recommends ca-certificates libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /uvx /bin/

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY apps/leaderboard ./apps/leaderboard
COPY packages/top-arena ./packages/top-arena
COPY infra ./infra

RUN uv sync --frozen --no-dev --package top-arena-leaderboard \
    && install -d -o 10001 -g 10001 /app/data

USER 10001:10001
EXPOSE 8910

CMD ["sh", "-c", "alembic -c infra/alembic.ini upgrade head && exec top-arena-server"]
