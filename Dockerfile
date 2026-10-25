ARG PYTHON_IMAGE=python:3.12-slim
ARG UV_IMAGE=ghcr.io/astral-sh/uv:0.12.5

FROM ${UV_IMAGE} AS uv

FROM ${PYTHON_IMAGE} AS builder
COPY --from=uv /uv /uvx /bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=0
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE NOTICE THIRD_PARTY.md ./
RUN uv sync --locked --no-dev --extra telegram --no-editable --no-install-project
COPY src ./src
RUN uv sync --locked --no-dev --extra telegram --no-editable

FROM ${PYTHON_IMAGE}
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    APP_DATA_DIR=/data \
    APP_DATABASE_URL=sqlite:////data/media_automation.sqlite3 \
    APP_BACKUP_DIR=/backups \
    APP_DOWNLOAD_INCOMPLETE=/downloads/incomplete \
    APP_DOWNLOAD_INBOX=/downloads/inbox \
    APP_MOVIE_ROOT=/media/movies \
    APP_TV_ROOT=/media/tv \
    APP_TELEGRAM_SESSION=/data/sessions/telegram.session
RUN useradd --create-home --uid 10001 app
WORKDIR /app
COPY --from=builder --chown=app:app /app /app
RUN mkdir -p /data /backups /downloads/incomplete /downloads/inbox /media/movies /media/tv \
    && chown -R app:app /data /backups /downloads /media
USER app
EXPOSE 8080
CMD ["mediactl", "serve", "--host", "0.0.0.0", "--port", "8080"]
