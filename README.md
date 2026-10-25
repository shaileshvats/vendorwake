# Vendorwake

**The upstream changed. We caught it.**

Vendorwake is an open-source, open-weight-AI dependency intelligence platform. It watches the vendors,
APIs and SDKs your software depends on, detects meaningful upstream changes, and maps each one to the code
in your repository that it could affect. Every finding is labelled **confirmed**, **potential** or
**unaffected**, and backed by evidence you can inspect.

**Live demo:** https://vendorwake-web.onrender.com (free Render tier; the first request after a quiet
period takes about a minute) · **API docs:** https://vendorwake-api.onrender.com/api/docs ·
**Write-up:** [the DEV post](https://dev.to/shaileshvats2004/vendorwake-the-upstream-changed-gemma-caught-it-and-a-test-proved-it-3o3f)

> Status: working end to end on free tiers. The hosted demo investigates a real upstream change,
> stripe-python 15.6.1 → 16.0.0, against [a small billing fixture](fixtures/stripe-billing-demo): three
> files confirmed by a test that passes on 15.6.1 and fails on 16.0.0, one file unaffected, and proposed
> patches that are re-tested in a sandbox (one of Gemma's patches fails, and the page says so).

## How it works

Deterministic code builds the evidence; an open-weight model (Gemma 4) interprets it. The model can explain
and downgrade a finding, but it cannot upgrade one: **confirmed** needs deterministic proof, enforced in code.

1. **Watch upstream.** SerpApi finds candidate announcements; Vendorwake only trusts text it fetched
   itself from the vendor's official domains (plain HTTP, or a Browserbase session for JavaScript-rendered
   docs). Every fetch is hashed into a TimescaleDB snapshot.
2. **Extract what changed.** Gemma turns release notes into structured changes with a verbatim evidence
   quote. Quotes and symbols that aren't literally in the source are flagged, never trusted.
3. **Map it to your code.** Manifest parsers and a Python AST scanner find the files, lines and calls that
   use each affected symbol and version.
4. **Prove it.** The repository's tests run on the old and new version in a sandboxed virtualenv; Gemma
   proposes a patch per file and the tests run again. Patches are proposed, never pushed.

| Sponsor | Job in Vendorwake |
|---|---|
| Temporal | Durable workflows for analysis, discovery, investigation and verification |
| Tiger Data | PostgreSQL + TimescaleDB snapshots + pgvector, row-level security per user |
| Auth0 | User login, plus one least-privilege machine identity per agent (no agent holds `repo:write`) |
| Backboard | Investigation memory for follow-up questions, with the memories used shown in the UI |
| Browserbase | Real browser sessions for JavaScript-rendered vendor docs |
| SerpApi | Discovery of candidate announcements (labelled "discovery, not proof") |
| Sentry | Agent tracing with `gen_ai` spans and real token counts; prompt text is never attached |
| Entire | The migration agent's session recorded as an Entire checkpoint ([adapter](tools/entire-agent-vendorwake)) |

Known limits, said plainly: GitHub access uses a read-only token rather than Auth0 Token Vault; sandbox
verification runs only for allow-listed repositories; on free Render, Temporal runs embedded and loses its
history on restart; and pgvector "related past changes" is off on the 512 MB hosted instance.

## Quick start (local)

Requirements: Docker with Compose. Nothing else on the host.

```sh
cp .env.example .env
docker compose up --build -d
```

For investigations, set `VW_LLM_API_KEY` to a free Google AI Studio key (Gemma 4). The other sponsor keys
in `.env.example` are optional; a step whose sponsor isn't configured reports that instead of
failing silently.

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
Frontend checks run in Docker: `npm run lint`, `npm run typecheck`, `npm run build` (see the frontend README).

## Repository layout

```
backend/     FastAPI API, Temporal workflows and worker, analysis (manifests, AST), LLM adapter,
             sponsor integrations (vendorwake/integrations), Alembic migrations, tests
frontend/    Next.js (App Router) dashboard with Auth0 login
fixtures/    stripe-billing-demo: the fixture repository the demo investigates
infra/       PostgreSQL 17 + TimescaleDB + pgvector image
scripts/     Provisioning (Auth0, Tiger Cloud, Render) and a preflight check
tools/       entire-agent-vendorwake: Entire agent-integration adapter
docs/        Deployment, API contract, M0 spike findings
spikes/      M0 spike code (throwaway experiments, kept for reference)
```

## Documentation

- [Deploying on free tiers](docs/deploy.md): Render, Tiger Cloud, Auth0 and embedded Temporal.
- [API contract](docs/api-contract.md)
- [M0 spike findings](docs/m0-findings.md): verified sponsor capabilities, the stripe-python 16.0.0 demo
  fixture, and decisions for Auth0, Backboard, Entire, Sentry and Gemma hosting.
- [Migrations and Tiger Cloud notes](backend/migrations/README.md)

## License

[Apache-2.0](LICENSE)
