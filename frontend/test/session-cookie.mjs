// Prints an encrypted Auth0 session cookie value (cookie name "__session") for local checks of the
// signed-in UI against test/mock-api.mjs. Test-only. Needs AUTH0_SECRET (and AUTH0_AUDIENCE) to match the
// running frontend. The cookie is valid for one hour.
//
//   AUTH0_SECRET=... AUTH0_AUDIENCE=... node test/session-cookie.mjs
import { generateSessionCookie } from "@auth0/nextjs-auth0/testing";

const secret = process.env.AUTH0_SECRET;
if (!secret) {
  console.error("AUTH0_SECRET is required");
  process.exit(1);
}

const now = Math.floor(Date.now() / 1000);
const cookie = await generateSessionCookie(
  {
    user: { sub: "auth0|mock-user", name: "Mock Developer", email: "dev@example.com" },
    tokenSet: {
      accessToken: "mock-access-token",
      expiresAt: now + 3600,
      audience: process.env.AUTH0_AUDIENCE,
      scope: "openid profile email offline_access",
    },
    internal: { sid: "mock-sid", createdAt: now },
  },
  { secret },
);
process.stdout.write(cookie);
