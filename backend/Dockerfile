# Vendorwake API image. Also used by the docker-compose `migrate` service (alembic upgrade head).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN groupadd --system --gid 10001 vendorwake \
    && useradd --system --uid 10001 --gid vendorwake --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin vendorwake

WORKDIR /app

# Dependencies first, from pyproject.toml alone, so code changes don't reinstall them.
COPY pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/pip \
    python -c "import tomllib; d = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; open('/tmp/requirements.txt', 'w').write('\n'.join(d))" \
    && pip install -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# The whole backend dir (package, alembic.ini, migrations/), owned by root and read-only to the app user.
COPY . .
RUN --mount=type=cache,target=/root/.cache/pip pip install --no-deps . \
    && python -c "import vendorwake.api.app"

USER vendorwake
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "vendorwake.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
