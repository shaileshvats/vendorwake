import { Auth0Client } from "@auth0/nextjs-auth0/server";

/**
 * Auth0 SDK client (@auth0/nextjs-auth0 v4).
 *
 * Reads AUTH0_DOMAIN, AUTH0_CLIENT_ID, AUTH0_CLIENT_SECRET, AUTH0_SECRET and APP_BASE_URL
 * from the environment. Requesting an access token for AUTH0_AUDIENCE lets server code call
 * the Vendorwake API with `Authorization: Bearer <token>`. `offline_access` yields a refresh
 * token (the Auth0 API must have "Allow Offline Access" enabled).
 *
 * Routes mounted by the proxy: /auth/login, /auth/logout, /auth/callback, /auth/profile,
 * /auth/access-token, /auth/backchannel-logout.
 */
export const auth0 = new Auth0Client({
  authorizationParameters: {
    audience: process.env.AUTH0_AUDIENCE || undefined,
    scope: "openid profile email offline_access",
  },
  signInReturnToPath: "/dashboard",
  // Server code reads tokens directly; the browser never needs them.
  enableAccessTokenEndpoint: false,
});
