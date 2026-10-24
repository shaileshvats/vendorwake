# Vendorwake

**The upstream changed. We caught it.**

Vendorwake is an open-source, open-weight-AI dependency intelligence platform. It watches the vendors,
APIs and SDKs your software depends on, detects meaningful upstream changes, and maps each one to the code
in your repository that it could affect. Every finding is labelled **confirmed**, **potential** or
**unaffected**, and backed by evidence you can inspect.

> Status: early development. M0 (integration spikes) and M1 (foundation: API, database, auth, app shell)
> are done. Repository analysis, change discovery and impact investigations land in M2–M4.

## How it works (target design)

Deterministic code builds the evidence (manifest parsing, import/AST scans, version-range checks,
sandboxed tests); an open-weight model (Gemma) classifies changes, extracts identifiers and explains
risk. A finding is only **confirmed** with deterministic proof. Long-running investigations are Temporal
workflows; data lives in PostgreSQL/TimescaleDB with pgvector (Tiger Data), with row-level security per user.

## Quick start (local)

Requirements: Docker with Compose. Nothing else on the host.

```sh
cp .env.example .env
docker compose up --build -d
```

| Service | URL |
|---|---|
| Web app | http://localhost:3000 |
| API + OpenAPI docs | http://localhost:8000/api/docs |
| Health | http://localhost:8000/api/health |
| Temporal UI | http://localhost:8233 |
| PostgreSQL | `127.0.0.1:5432` |

The `migrate` service applies database migrations before the API starts. Without Auth0 settings the web
app shows a signed-out page that lists the missing variables; protected API endpoints fail closed.

### Enabling sign-in (Auth0)

1. Create an Auth0 **Regular Web Application**: callback `http://localhost:3000/auth/callback`,
   logout URL `http://localhost:3000`.
2. Create an Auth0 **API** with identifier `https://api.vendorwake.local` (RS256).
3. Fill `AUTH0_DOMAIN`, `AUTH0_CLIENT_ID`, `AUTH0_CLIENT_SECRET`, `AUTH0_SECRET` (`openssl rand -hex 32`),
   `AUTH0_AUDIENCE`, `VW_AUTH0_DOMAIN`, `VW_AUTH0_AUDIENCE` in `.env`, then `docker compose up -d --build`.

More detail: [frontend/README.md](frontend/README.md).

## Tests

```sh
# Backend unit tests (Python 3.12 in Docker; no services needed)
docker run --rm -v "$PWD/backend":/src:ro python:3.12-slim bash -c \
  "cp -r /src /app && cd /app && pip install -q -e '.[dev]' && pytest tests/unit -q"
```

The integration suite (migrations up/down, extensions, row-level security isolation) needs a real
database; the exact Docker commands are in [backend/tests/integration/README.md](backend/tests/integration/README.md).
Frontend checks run in Docker: `npm run lint`, `npx tsc --noEmit`, `npm run build` (see the frontend README).

## Repository layout

```
backend/     FastAPI app (vendorwake/api), settings & security (vendorwake/core),
             database models/sessions (vendorwake/db), Alembic migrations, tests
frontend/    Next.js (App Router) dashboard with Auth0 login
infra/       PostgreSQL 17 + TimescaleDB + pgvector image
docs/        Design notes, including the M0 spike findings
spikes/      M0 spike code (throwaway experiments, kept for reference)
```

## Documentation

- [M0 spike findings](docs/m0-findings.md): verified sponsor capabilities, the stripe-python 16.0.0 demo
  fixture, and decisions for Auth0, Backboard, Entire, Sentry and Gemma hosting.
- [Migrations and Tiger Cloud notes](backend/migrations/README.md)

## License

[Apache-2.0](LICENSE)
