# Database migrations

Alembic migrations for the Vendorwake schema. Run them from `backend/`:

```sh
VW_MIGRATION_DATABASE_URL=postgresql+psycopg://<owner>:<password>@<host>:5432/<db> alembic upgrade head
```

The URL always comes from `vendorwake.core.config.get_settings().migration_database_url`; `alembic.ini` holds
no connection string. `alembic upgrade head --sql` prints the DDL without connecting.

## Roles

| Role | Used by | Notes |
| --- | --- | --- |
| owner (`POSTGRES_USER` locally, `tsdbadmin` on Tiger Cloud) | migrations | Owns every table. |
| `vendorwake_app` | API and workers (`VW_DATABASE_URL`) | LOGIN, NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, NOCREATEROLE. Owns nothing. |
| worker role (later) | ingestion workers | Will get INSERT/UPDATE on the global tables (`vendors`, `monitoring_sources`, `vendor_changes`). Today `vendorwake_app` can only SELECT them. |

`0001` stops with a clear error if `vendorwake_app` does not exist. It does not create the role, because a
role is cluster-wide and its password does not belong in a migration.

## Row-level security

Tenant tables (`users`, `repositories`, `integrations`, `investigations`, `impact_evidence`,
`recommendations`, `agent_sessions`) have `ENABLE` and `FORCE ROW LEVEL SECURITY`, and one policy each:

- `users`: `id = app.current_user_id`, or `auth_provider_id = app.current_auth_subject`. The second case
  lets the API resolve or create the caller's own row on first login (`vendorwake.db.session.upsert_user`).
- all other tables: `user_id = app.current_user_id`

The settings are read with `NULLIF(current_setting(name, true), '')`. If no scope is set, a query matches
no rows; it does not raise an error. `vendorwake.db.session.tenant_session()` sets the scope per
transaction with `set_config(..., true)`.

Grants are written out per table, with no `ALTER DEFAULT PRIVILEGES`. A table added later stays
unreachable by the app role until its migration grants access and, for tenant data, enables RLS. The
primary keys are UUIDs, so there are no sequences to grant.

The child tables use composite foreign keys `(parent_id, user_id) -> parent(id, user_id)`. A child row
cannot belong to a different tenant than its parent row, and foreign-key checks bypass RLS.

## Local (docker compose)

`infra/postgres` builds PostgreSQL 17 with TimescaleDB and pgvector. Its init script
(`/docker-entrypoint-initdb.d/10-vendorwake.sh`) runs once, on an empty data volume. It creates
`vendorwake_app` with the password in `VW_APP_DB_PASSWORD` and enables the extensions. The `migrate` service
then runs `alembic upgrade head` as the owner.

## Tiger Cloud

Tiger Cloud runs no init scripts. The default `tsdbadmin` user is not a superuser, but it can create roles
with ordinary SQL. TimescaleDB, pgvector and pgvectorscale are available. Do this once per service, before
the first migration:

```sql
-- as tsdbadmin; pick the password from your secret store, never commit it
CREATE ROLE vendorwake_app LOGIN PASSWORD '<secret>'
    NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOREPLICATION NOINHERIT;
GRANT CONNECT ON DATABASE tsdb TO vendorwake_app;
GRANT USAGE ON SCHEMA public TO vendorwake_app;
```

Then set `VW_MIGRATION_DATABASE_URL` to the `tsdbadmin` connection string, add `?sslmode=require`, and
run `alembic upgrade head`. The `CREATE EXTENSION IF NOT EXISTS` statements do nothing where the
extensions are already installed.

Differences from local to keep in mind:

- On Tiger Cloud the owner is not a superuser, so `FORCE ROW LEVEL SECURITY` applies to `tsdbadmin` too.
  A data migration or backfill that runs as the owner sees no tenant rows unless it sets
  `app.current_user_id`. Locally the owner is a superuser and bypasses RLS; the integration tests rely on
  that when they seed data.
- Creating a `BYPASSRLS` role requires a superuser in PostgreSQL. Don't plan an admin path that needs one
  on Tiger Cloud. Use the per-tenant scope or a SECURITY DEFINER function instead. This has not been
  tested on Tiger Cloud.
- `downgrade()` leaves the extensions in place. On Tiger Cloud the platform manages them.
