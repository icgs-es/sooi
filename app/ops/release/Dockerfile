ARG BASE_IMAGE=python:3.12-slim
FROM ${BASE_IMAGE}

ARG SOURCE_COMMIT
ARG SOURCE_TREE_ID
ARG SOURCE_ARCHIVE_SHA256
ARG BASE_IMAGE_ID
ARG SOURCE_DATE_EPOCH

LABEL org.opencontainers.image.revision="${SOURCE_COMMIT}" \
      org.opencontainers.image.created.epoch="${SOURCE_DATE_EPOCH}" \
      io.sooi.source.tree="${SOURCE_TREE_ID}" \
      io.sooi.source.archive.sha256="${SOURCE_ARCHIVE_SHA256}" \
      io.sooi.base.image.id="${BASE_IMAGE_ID}" \
      io.sooi.release-contract="WP-01-P0-B+WP-02-P0-E"

ENV DJANGO_SETTINGS_MODULE=config.settings.prod \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY infra/django/requirements.lock /tmp/requirements.lock
RUN python -m pip install --no-cache-dir --requirement /tmp/requirements.lock \
    && python -m pip check \
    && python -c "import django, gunicorn, celery, redis, requests, psycopg" \
    && rm -f /tmp/requirements.lock
COPY . /app

# SOOI_BACKUP_HYGIENE_V1
RUN set -eu; \
    for root in /app /app; do \
      if [ -d "$root" ]; then \
        find "$root" -xdev -type f \
          \( -name '*.bak' -o -name '*.bak.*' -o -name '*.bak_*' \
             -o -name '*.backup' -o -name '*.backup.*' -o -name '*.backup_*' \) -delete; \
      fi; \
    done; \
    for root in /app /app; do \
      if [ -d "$root" ]; then \
        remaining="$(find "$root" -xdev -type f \
          \( -name '*.bak' -o -name '*.bak.*' -o -name '*.bak_*' \
             -o -name '*.backup' -o -name '*.backup.*' -o -name '*.backup_*' \) -print -quit)"; \
        test -z "$remaining"; \
      fi; \
    done

RUN groupadd --system --gid 10001 sooi \
    && useradd --system --uid 10001 --gid sooi --home-dir /app --shell /usr/sbin/nologin sooi \
    && mkdir -p /app/media /app/staticfiles \
    && chown -R 10001:10001 /app
USER 10001:10001

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
