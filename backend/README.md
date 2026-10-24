# Vendorwake backend

FastAPI API, database layer (SQLAlchemy + Alembic on PostgreSQL/TimescaleDB/pgvector) and, from M4, the
Temporal worker. See the repository README for the full local setup.

- API app: `vendorwake.api.app:app` (OpenAPI docs at `/api/docs`)
- Settings: `vendorwake/core/config.py` (environment variables prefixed `VW_`, see `../.env.example`)
- Migrations: `alembic upgrade head` (uses `VW_MIGRATION_DATABASE_URL`); notes in `migrations/README.md`
- Tests: `pytest tests/unit` (no services needed) and `pytest -m integration tests/integration`
  (needs Postgres; commands in `tests/integration/README.md`)
