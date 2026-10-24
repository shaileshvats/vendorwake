#!/usr/bin/env bash
# First-start initialisation for the local Vendorwake database (run by the official postgres entrypoint
# only when the data directory is empty).
#
# - POSTGRES_USER stays the superuser and owns the schema; migrations run as this role.
# - vendorwake_app is the runtime role for the API/worker: it owns nothing and is subject to
#   row-level security (no SUPERUSER, no BYPASSRLS). Table privileges are granted by the migrations.
# - Extensions are also created (IF NOT EXISTS) by the first migration, so this is only a convenience.
#
# The password is passed as a psql variable and quoted with format(%L); it is never spliced into SQL text.
set -euo pipefail

if [[ -z "${VW_APP_DB_PASSWORD:-}" ]]; then
    echo "10-vendorwake.sh: VW_APP_DB_PASSWORD must be set to create the vendorwake_app role" >&2
    exit 1
fi

psql -v ON_ERROR_STOP=1 \
    --username "$POSTGRES_USER" \
    --dbname "$POSTGRES_DB" \
    --set app_password="$VW_APP_DB_PASSWORD" \
    --set db_name="$POSTGRES_DB" <<'SQL'
SELECT format(
    'CREATE ROLE vendorwake_app LOGIN PASSWORD %L NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT',
    :'app_password'
)
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'vendorwake_app')
\gexec

GRANT CONNECT ON DATABASE :"db_name" TO vendorwake_app;
GRANT USAGE ON SCHEMA public TO vendorwake_app;

CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE EXTENSION IF NOT EXISTS vector;
SQL
