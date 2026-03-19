# gke fastapi backend

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
RUN apt-get update && apt-get install -y curl
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-backend uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim-bookworm

RUN groupadd -g 1000 appuser && \
    useradd -r -u 1000 -g appuser appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /opt/venv-backend /opt/venv-backend
COPY --chown=appuser:appuser . .

RUN mkdir -p /app/data && chown appuser:appuser /app/data

USER appuser
EXPOSE 8000

CMD ["/opt/venv-backend/bin/uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]


