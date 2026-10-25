# Deploying Vendorwake (free tier only)

The whole stack runs on free plans: Render (two free web services), Tiger Cloud (free service),
Auth0 (free tenant), and Temporal as either a Temporal Cloud trial namespace or a dev server embedded
in the API container. All provisioning scripts are idempotent, so you can re-run any step.

| Piece | Where | Plan |
|---|---|---|
| `vendorwake-api`: FastAPI + Temporal worker (+ optional embedded Temporal) | Render web service, `backend/Dockerfile.worker`, build context = repo root | Free |
| `vendorwake-web`: Next.js | Render web service, `frontend/Dockerfile`, context `frontend/` | Free |
| PostgreSQL + TimescaleDB + pgvector | Tiger Cloud | Free (shared compute, 750 MiB) |
| Login, API scopes, agent identities | Auth0 | Free |
| Workflows | Temporal Cloud trial (API key) **or** embedded `temporal server start-dev` | Trial / free |

Render's free tier has no background workers. That is why the worker runs inside the API container,
started by `scripts/container/launch.py`. If uvicorn, the worker or the embedded Temporal server
exits, the launcher stops the other processes and exits, and Render restarts the container.

## Running the scripts

The host only has Python 3.9, so the scripts run in `python:3.12-slim` from the repo root:

```sh
alias vwpy='docker run --rm -it -v vw-pip-cache:/root/.cache/pip -v "$PWD":/repo -w /repo python:3.12-slim \
  sh -c "pip install -q -r scripts/requirements.txt && python \"\$@\"" --'
```

`.env` holds every secret and stays mode 600. The scripts update it in place, and they never print
secret values (dry-runs show `<secret, N chars>`).

## Steps, in order

0. **One-time manual setup** (dashboards; none of this can be scripted with the keys we have):
   - **Tiger Cloud**: create a free service (us-east-1). Copy the `tsdbadmin` connection string into
     `VW_MIGRATION_DATABASE_URL` as `postgresql+psycopg://tsdbadmin:<pw>@<host>:<port>/tsdb?sslmode=require`.
   - **Auth0**: create a tenant. Under Applications, create a *Machine to Machine* app authorized for the
     **Auth0 Management API** with these scopes: `read:resource_servers create:resource_servers update:resource_servers read:clients create:clients update:clients read:client_keys read:client_grants create:client_grants update:client_grants`.
     Put its credentials in `AUTH0_MGMT_CLIENT_ID` / `AUTH0_MGMT_CLIENT_SECRET`, and the canonical tenant
     domain (not a custom domain) in `AUTH0_DOMAIN`.
   - **Render**: create an API key (Account settings → API keys) and put it in `RENDER_API_KEY`. Then
     **link GitHub to Render** (Dashboard → New → connect GitHub, grant access to `shaileshvats/vendorwake`).
     Render only auto-deploys from a linked Git provider. Its docs reserve the "public Git URL" method for
     repos you don't belong to, and that method doesn't support auto-deploy. If you skip the link, run
     `deploy_render.py --no-auto-deploy` and redeploy by re-running the script (untested whether the
     API accepts the repo then).
   - **Temporal**, choose one:
     - *Temporal Cloud trial*: create a namespace with API-key auth. Set `VW_TEMPORAL_ADDRESS=<ns>.<acct>.tmprl.cloud:7233`,
       `VW_TEMPORAL_NAMESPACE=<ns>.<acct>` and `VW_TEMPORAL_API_KEY`. The trial terms (credits, expiry,
       card requirement) weren't confirmed from Temporal's own pages, so check them before signing up.
     - *Embedded*: set `VW_TEMPORAL_ADDRESS=localhost:7233` and `VW_TEMPORAL_EMBEDDED=true`. It costs
       nothing, but history is in memory and is **lost on every spin-down, restart and deploy**.
   - Fill the remaining keys (OpenRouter, SerpApi, Browserbase, Backboard, Sentry, GitHub PAT, `VW_ENCRYPTION_KEY`).
1. **Preflight**: `vwpy scripts/preflight.py`. This runs one read-only call per credential and prints a
   ✓/✗/– table with free-tier allowances where they're cheap to read: SerpApi searches left, OpenRouter
   credit, GitHub rate limit, Tiger storage against 750 MiB, Browserbase concurrency, missing Auth0
   Management scopes.
2. **Tiger bootstrap**: `vwpy scripts/tiger_bootstrap.py --dry-run`, then without `--dry-run`. It creates the
   `timescaledb` and `vector` extensions and the `vendorwake_app` / `vendorwake_worker` roles
   (NOBYPASSRLS, sent as SCRAM verifiers), checks that both can log in, generates `VW_APP_DB_PASSWORD` /
   `VW_WORKER_DB_PASSWORD` if they're empty, and writes `VW_DATABASE_URL` / `VW_WORKER_DATABASE_URL`.
3. **Auth0 (first pass)**: `vwpy scripts/auth0_setup.py --dry-run`, then without it. It creates the API
   (`VW_AUTH0_AUDIENCE`; scopes `docs:read repo:read patch:propose repo:write`; RS256; offline access;
   RBAC + permissions claim), the "Vendorwake Web" app (localhost callbacks), and M2M apps for
   `discovery`, `repository` and `migration` with their client grants. It writes `AUTH0_CLIENT_ID/SECRET`,
   `AUTH0_SECRET` and `VW_AUTH0_AGENT_CREDENTIALS`.
4. **Migrate** (free Render web services can't run pre-deploy commands, so this runs from your machine):
   ```sh
   docker build -t vendorwake-api backend
   docker run --rm --env-file .env vendorwake-api alembic upgrade head
   ```
   Migration 0002 also sets `vendorwake_worker`'s password from `VW_WORKER_DB_PASSWORD`, which is the
   same value tiger_bootstrap wrote. The alternative, `deploy_render.py --migrate-on-start`, runs Alembic
   at every container start, but it has to send the owner URL to Render, so avoid it.
5. **Deploy**: `vwpy scripts/deploy_render.py --dry-run`, then without it. This creates or updates
   `vendorwake-api` and `vendorwake-web` (free plan, region `virginia`, next to Tiger's us-east-1), pushes
   each service's env vars, feeds the web URL back into `VW_CORS_ORIGINS` / `APP_BASE_URL`, polls the
   deploys, and writes `RENDER_API_URL` / `RENDER_WEB_URL` / `RENDER_OWNER_ID` to `.env`. The first
   frontend build can take several minutes on a free instance.
6. **Auth0 (second pass)**: `vwpy scripts/auth0_setup.py`. It picks up `RENDER_WEB_URL` and adds
   `<web>/auth/callback`, the logout URL and the web origin, keeping the localhost entries.
7. **Smoke test**:
   - `curl -s $RENDER_API_URL/api/health` returns `{"status":"ok", "checks":{"database":"ok","temporal":"ok"}}`.
     The first request after idle can take about a minute while the service wakes.
   - Open `$RENDER_WEB_URL` → Sign in → `/dashboard`. The System status panel shows your subject and scopes.
   - Add the demo repository and check that its analysis workflow completes (this exercises the worker).
   - `vwpy scripts/preflight.py` again: everything should be ✓ or –.

Re-run step 5 after changing `.env`. It only pushes what changed: runtime variables are applied with
`deployMode: deploy_only`, and a changed `NEXT_PUBLIC_*` triggers a rebuild. Code changes deploy
automatically on push to `main`.

## What each service receives

- **api**: every `VW_*` key except `VW_MIGRATION_DATABASE_URL`, `VW_APP_DB_PASSWORD`, `VW_WORKER_DB_PASSWORD`;
  plus `PORT=8000`, `VW_ENV=production` (when `.env` says `local`) and `VW_CORS_ORIGINS=<web URL>`.
- **web**: `AUTH0_DOMAIN AUTH0_CLIENT_ID AUTH0_CLIENT_SECRET AUTH0_SECRET AUTH0_AUDIENCE`, any `NEXT_PUBLIC_*`,
  `PORT=3000`, `APP_BASE_URL=<web URL>`, `NEXT_PUBLIC_API_BASE_URL=<api URL>`. Render passes env vars to
  Docker builds as build args, and only `NEXT_PUBLIC_API_BASE_URL` is referenced in the Dockerfile.
- **Never sent**: `RENDER_*`, `AUTH0_MGMT_*`, `POSTGRES_*`, the owner DB URL, role passwords. The env PUT
  replaces a service's whole variable list, so `.env` is the source of truth. Variables added by hand in
  the dashboard are removed on the next run.

## Costs

$0. Render free web services: 750 instance-hours per workspace per month, shared between both services.
They don't consume hours while spun down. Tiger free service: 750 MiB, after which it goes read-only.
Auth0 free: Token Vault yes, CIBA no. Temporal: the Cloud trial or the embedded server. Paid upgrades
that would remove the limitations below (not used): Render Starter instances (about $7/month each,
from third-party sources; see render.com/pricing) for always-on, pre-deploy migrations and a separate worker.

## Limitations of the free setup

- **Spin-down**: a free web service sleeps after 15 minutes without inbound HTTP traffic and takes
  about a minute to wake. The worker sleeps with it. Scheduled monitoring and running workflows pause
  until the next request wakes the API. With Temporal Cloud, workflows resume after wake-up. With the
  embedded server, they are **gone**.
- **Ephemeral disk**: anything written locally (git worktrees, Entire session dirs, embedded Temporal
  state) is lost on spin-down and deploy. Durable state lives only in Tiger and Temporal Cloud.
- **512 MB RAM, small CPU share** on the free instance, shared by uvicorn, the worker, git/Entire and
  optionally Temporal. Locally the API plus embedded Temporal used about 150 MB at idle, so heavy
  analysis jobs may run out of memory.
- No shell or one-off jobs on free instances, and only one instance.
- The combined image is about 790 MB with the Temporal CLI. Build with `--build-arg INSTALL_TEMPORAL_CLI=false`
  to drop it when you use Temporal Cloud. The Dockerfile needs the repo root as build context:
  `docker build -f backend/Dockerfile.worker -t vendorwake-app .`
