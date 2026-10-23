FROM ghcr.io/astral-sh/uv:0.6.0 AS uv

FROM python:3.12.9-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH

WORKDIR /app

COPY services/front/pyproject.toml services/front/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

RUN groupadd --system --gid 10001 app \
    && useradd --system --no-log-init --uid 10001 --gid 10001 --no-create-home --home-dir /app app
RUN mkdir /app/.nicegui && chown app:app /app/.nicegui

COPY services/front/app ./app

USER 10001:10001
EXPOSE 8080
CMD ["python", "-m", "app.main"]
