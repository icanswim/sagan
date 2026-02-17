# gke uv streamlit frontend

FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder
RUN apt-get update && apt-get install -y curl
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    UV_PROJECT_ENVIRONMENT=/opt/venv-frontend uv sync --frozen --no-install-project --no-dev

FROM python:3.12-slim-bookworm
WORKDIR /app

#RUN groupadd -r appuser && useradd -r -g appuser appuser

COPY --from=builder /opt/venv-frontend /opt/venv-frontend
COPY . .

#COPY --chown=appuser:appuser . .
#USER appuser

EXPOSE 8501

#CMD ["/opt/venv-frontend/bin/streamlit", "run", "main.py", "--server.port=8501", "--server.address=0.0.0.0"]

