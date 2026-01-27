# --- Stage 1: Builder ---
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# Install Backend
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=app/backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=app/backend/uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-backend uv sync --frozen --no-install-project --no-dev

# Install Frontend
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=app/frontend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=app/frontend/uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-frontend uv sync --frozen --no-install-project --no-dev

# --- Stage 2: Runtime ---
FROM python:3.12-slim-bookworm
WORKDIR /app

# 1. Best Practice: Security (Non-root user)
RUN groupadd -r appuser && useradd -r -g appuser appuser

# 2. Copy environments
COPY --from=builder /opt/venv-backend /opt/venv-backend
COPY --from=builder /opt/venv-frontend /opt/venv-frontend

# 3. Copy source code
COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000
EXPOSE 8501

# This is a placeholder; your deployment.yaml will override this
CMD ["python", "app/backend/main.py"]


