import "server-only";

/**
 * Environment variables @auth0/nextjs-auth0 v4 needs before it can run a login flow.
 * (The SDK only logs a warning when these are missing, so we check explicitly and
 * render a signed-out state instead of failing at request time.)
 */
const REQUIRED_AUTH0_ENV = [
  "AUTH0_DOMAIN",
  "AUTH0_CLIENT_ID",
  "AUTH0_CLIENT_SECRET",
  "AUTH0_SECRET",
] as const;

export interface AuthConfigStatus {
  configured: boolean;
  /** Names (never values) of required variables that are unset. */
  missing: string[];
  /** Whether AUTH0_AUDIENCE is set, which is required to obtain an access token for the API. */
  audienceConfigured: boolean;
}

export function getAuthConfigStatus(): AuthConfigStatus {
  const missing = REQUIRED_AUTH0_ENV.filter((name) => !process.env[name]?.trim());
  return {
    configured: missing.length === 0,
    missing,
    audienceConfigured: Boolean(process.env.AUTH0_AUDIENCE?.trim()),
  };
}
