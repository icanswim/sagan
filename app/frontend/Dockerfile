# Stage 1: Builder
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    uv sync --frozen --no-install-project --no-dev --no-editable

# Stage 2: Runtime
FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1

RUN groupadd -g 1000 appuser && \
    useradd -r -u 1000 -g appuser -m appuser

COPY --from=builder --chown=appuser:appuser /app/.venv /app/.venv
COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8501

CMD ["/app/.venv/bin/streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]
