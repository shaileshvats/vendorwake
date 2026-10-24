import { NextResponse, type NextRequest } from "next/server";

import { auth0 } from "@/lib/auth0";
import { getAuthConfigStatus } from "@/lib/auth-config";

/**
 * Next.js 16 proxy (formerly middleware). Mounts the Auth0 SDK routes under /auth/* and
 * keeps the rolling session cookie fresh on every other request.
 */
export async function proxy(request: NextRequest) {
  const { configured } = getAuthConfigStatus();
  const isAuthRoute = request.nextUrl.pathname.startsWith("/auth/");

  if (!configured) {
    // Without Auth0 credentials the app still renders its signed-out state; only the SDK
    // routes are unavailable.
    if (isAuthRoute) {
      return new NextResponse(
        "Authentication is not configured on this server. See frontend/README.md.",
        { status: 503, headers: { "content-type": "text/plain; charset=utf-8" } },
      );
    }
    return NextResponse.next();
  }

  const response = await auth0.middleware(request);

  // Server Components cannot write cookies, so refresh the API access token here when it
  // is about to expire and persist the updated token set onto the response
  // (recommended by the SDK's getAccessToken() documentation).
  if (!isAuthRoute && process.env.AUTH0_AUDIENCE) {
    const session = await auth0.getSession(request);
    if (session) {
      try {
        await auth0.getAccessToken(request, response);
      } catch {
        // Surfaced to the user by the API status panel; nothing to do here.
      }
    }
  }

  return response;
}

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|icon.svg|sitemap.xml|robots.txt).*)"],
};
