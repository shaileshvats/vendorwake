# Database integration tests

These tests run against a real database built from `infra/postgres`, as two roles. The owner applies the
migrations, and `vendorwake_app` is restricted by RLS. Both URLs must be set; otherwise every test here
is skipped and the skip message names the missing variable.

- `VW_MIGRATION_DATABASE_URL`: the owner/superuser
- `VW_DATABASE_URL`: `vendorwake_app`

The suite runs `alembic upgrade head`, `downgrade base` and `upgrade head` again. Use a throwaway database.

## Run in Docker (no local Python or Postgres needed)

From the repository root:

```sh
docker build -t vendorwake-postgres:dev infra/postgres
docker network create vw-dbtest-net

OWNER_PW=$(openssl rand -hex 16); APP_PW=$(openssl rand -hex 16)
docker run -d --name vw-dbtest-pg --network vw-dbtest-net \
  -e POSTGRES_USER=vendorwake_owner -e POSTGRES_PASSWORD="$OWNER_PW" \
  -e POSTGRES_DB=vendorwake -e VW_APP_DB_PASSWORD="$APP_PW" \
  vendorwake-postgres:dev
until docker exec vw-dbtest-pg pg_isready -h 127.0.0.1 -U vendorwake_owner -d vendorwake; do sleep 1; done

docker run --rm --network vw-dbtest-net \
  -v vw-pip-cache:/root/.cache/pip \
  -v "$PWD/backend":/app:ro -w /app \
  -e PYTHONDONTWRITEBYTECODE=1 -e PYTHONPATH=/app \
  -e VW_MIGRATION_DATABASE_URL="postgresql+psycopg://vendorwake_owner:${OWNER_PW}@vw-dbtest-pg:5432/vendorwake" \
  -e VW_DATABASE_URL="postgresql+psycopg://vendorwake_app:${APP_PW}@vw-dbtest-pg:5432/vendorwake" \
  python:3.12-slim bash -c "pip install -q 'sqlalchemy>=2.0.36' 'psycopg[binary]>=3.2' 'alembic>=1.14' \
      'pydantic>=2.11' 'pydantic-settings>=2.6' 'pytest>=8.3' 'ruff>=0.8' \
    && ruff check --no-cache vendorwake/db migrations tests/integration \
    && python -m pytest -p no:cacheprovider -m integration tests/integration -v"

docker rm -f vw-dbtest-pg && docker network rm vw-dbtest-net
```

The database is never published on a host port. If you prefer to use the compose stack, run
`docker compose up -d postgres` and point both URLs at `127.0.0.1:5432` with the passwords from your `.env`.
