# Vendorwake frontend

Next.js 16 (App Router, Turbopack) + TypeScript (strict) + Tailwind CSS v4, with Auth0 login via
`@auth0/nextjs-auth0` v4. All API calls are made server-side; the browser never sees an access token.

```
app/                 routes: / (landing), (app)/dashboard, repositories[/id], vendors, changes[/id],
                     investigations[/id], settings, settings/security
components/          app shell, panels, status pills, evidence graph (plain SVG), diff viewer, forms
lib/auth0.ts         Auth0Client (requests an access token for AUTH0_AUDIENCE)
lib/api.ts           typed server-side fetch: Bearer token, timeouts, error mapping (never throws)
lib/endpoints.ts     GET /api/health, GET /api/me with runtime response validation
lib/vendorwake.ts    typed wrappers for every endpoint in docs/api-contract.md
lib/actions.ts       Server Actions for all mutations (token attached server-side)
test/                mock API + session-cookie script for local UI checks (not part of the app)
proxy.ts             Next.js 16 proxy (formerly middleware): mounts /auth/* and refreshes tokens
types/               API and session types
```

## Run locally (Docker, no Node.js on the host)

All commands use `node:22-alpine` and a named npm cache volume.

```sh
cd frontend
# install, check, build
docker run --rm -v vw-npm-cache:/root/.npm -v "$PWD":/app -w /app node:22-alpine \
  sh -c "npm ci && npm run lint && npx tsc --noEmit && npm run build"

# dev server on http://localhost:3000 (reads frontend/.env.local)
docker run --rm -it -p 3000:3000 -v vw-npm-cache:/root/.npm -v "$PWD":/app -w /app node:22-alpine \
  sh -c "npm run dev -- --hostname 0.0.0.0"
```

`node_modules` is installed inside the Linux container. Don't reuse it with a macOS Node install.

### Production image

```sh
docker build -t vendorwake-frontend \
  --build-arg NEXT_PUBLIC_API_BASE_URL=http://localhost:8000 .
docker run --rm -p 3000:3000 --env-file .env.local -e API_BASE_URL=http://host.docker.internal:8000 vendorwake-frontend
```

The image is multi-stage, uses `output: "standalone"`, runs as the unprivileged `node` user, and listens on port 3000.

**About `NEXT_PUBLIC_*`.** These values are inlined into the JavaScript bundle at **build time**, so
`NEXT_PUBLIC_API_BASE_URL` is a Docker build arg. Setting it at `docker run` has no effect on code that
was already compiled. Because every API call runs on the server, there is also a runtime-only
`API_BASE_URL` that takes precedence. Use it when the address the server can reach differs from the
public one, for example `http://api:8000` inside docker compose.

## Environment variables (`frontend/.env.local`)

| Variable | Required | Purpose |
|---|---|---|
| `AUTH0_DOMAIN` | yes* | Auth0 tenant domain, e.g. `your-tenant.us.auth0.com` |
| `AUTH0_CLIENT_ID` | yes* | Client ID of the Regular Web Application |
| `AUTH0_CLIENT_SECRET` | yes* | Client secret of that application |
| `AUTH0_SECRET` | yes* | Cookie encryption key: `openssl rand -hex 32` |
| `APP_BASE_URL` | recommended | `http://localhost:3000`. If unset, the SDK infers it from the request host |
| `AUTH0_AUDIENCE` | for API calls | Identifier of the Vendorwake API in Auth0, e.g. `https://api.vendorwake.local`. Must match the backend's `VW_AUTH0_AUDIENCE` |
| `NEXT_PUBLIC_API_BASE_URL` | build time | API base URL, default `http://localhost:8000` |
| `API_BASE_URL` | optional, runtime | Server-side override of the API base URL |

\* Without these, the app still builds and runs. The landing page shows "Sign-in is not available"
and lists the missing variable names, `/auth/*` returns 503, and every app route redirects to `/`.

The variable names match the root `.env.example`. `.env.local` is git-ignored.

## Auth0 configuration

1. **Application.** Create a **Regular Web Application**.
   - Allowed Callback URLs: `http://localhost:3000/auth/callback`
   - Allowed Logout URLs: `http://localhost:3000`
   - Allowed Web Origins: `http://localhost:3000`
   - Grant types: Authorization Code and Refresh Token (the default)
2. **API.** Create an API with identifier = `AUTH0_AUDIENCE` (RS256), and enable **Allow Offline
   Access** so the SDK receives a refresh token (the app requests `openid profile email offline_access`).
   In the application's APIs tab, authorize the application for this API if your tenant requires it.
3. Set the variables above, restart, open `http://localhost:3000`, and click **Sign in**. After the
   callback you land on `/dashboard`. The System status panel then calls `GET /api/me` with your
   token and shows the subject, email and scopes the API reports.

SDK routes (mounted by `proxy.ts`): `/auth/login`, `/auth/logout`, `/auth/callback`,
`/auth/profile`, `/auth/backchannel-logout`. `/auth/access-token` is disabled because tokens are only
used server-side.

## Implementation notes

- **Proxy.** Next.js 16 renamed `middleware.ts` to `proxy.ts`, which runs on the Node.js runtime.
  Besides mounting the SDK routes, the proxy calls `auth0.getAccessToken(req, res)` for signed-in
  requests. Server Components can't write cookies, so this is where a refreshed token set gets
  persisted, as the SDK recommends.
- **Cache Components is off.** `create-next-app` enables `cacheComponents` by default in 16.4, but
  every page here is per-user and read at request time. Session reads go through
  `getCurrentUser()`, which calls `connection()` to opt into dynamic rendering.
- **No placeholder data.** Sections without a backing endpoint render an empty state. Only the
  System status panel shows live data (`/api/health`, `/api/me`).
- **ImpactStatusBadge** (`components/impact-status-badge.tsx`) shows each impact status (confirmed,
  potential, unaffected, unknown) with a distinct icon shape, a text label and an Okabe-Ito hue,
  so it never relies on color alone.

## Checking the UI against a mock API

`test/mock-api.mjs` implements the contract with fixture data (stripe-python 16.0.0 scenario);
`test/session-cookie.mjs` mints a signed-in `__session` cookie with `@auth0/nextjs-auth0/testing`.
Run both in one container after `npm run build`, with dummy `AUTH0_*` values, `AUTH0_SECRET` set,
and `API_BASE_URL=http://127.0.0.1:8010`. Use `127.0.0.1` rather than `localhost` in the browser if
another app on `localhost` already set a `__session` cookie (cookies are not port-scoped).

## Not tested

- A real Auth0 login. There is no tenant yet, and with dummy credentials `/auth/login` returns 500
  because OIDC discovery against the fake domain fails. The signed-in shell was checked with a
  locally generated session cookie (`@auth0/nextjs-auth0/testing`) against a mock API.
- Token refresh and the `/api/me` flow against the real FastAPI backend.
