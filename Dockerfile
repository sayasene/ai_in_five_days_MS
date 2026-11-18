# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.6.14 /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml README.md ./
COPY pursuit_auditor ./pursuit_auditor

RUN uv sync --no-dev --extra gcp

FROM python:3.12-slim AS runtime

RUN groupadd --system appuser && useradd --system --gid appuser --create-home appuser

WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/pursuit_auditor /app/pursuit_auditor
COPY --from=builder /app/README.md /app/README.md

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PORT=8080

USER appuser
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz')"

CMD ["uvicorn", "pursuit_auditor.fast_api_app:fastapi_app", "--host", "0.0.0.0", "--port", "8080"]
