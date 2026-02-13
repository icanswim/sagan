# gke sidecar pattern
# uv streamlit frontend
# fastapi backend

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
RUN apt-get update && apt-get install -y curl
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

# backend
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=app/backend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=app/backend/uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-backend uv sync --frozen --no-install-project --no-dev

# frontend
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=app/frontend/pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=app/frontend/uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-frontend uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app

#RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=builder /opt/venv-backend /opt/venv-backend
COPY --from=builder /opt/venv-frontend /opt/venv-frontend
COPY ./app /app

#COPY --chown=appuser:appuser . .
#USER appuser

EXPOSE 8000
EXPOSE 8501

#CMD ["/opt/venv-backend/bin/uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
#CMD ["/opt/venv-frontend/bin/streamlit", "run", "frontend/main.py", "--server.port=8501", "--server.address=0.0.0.0"]

