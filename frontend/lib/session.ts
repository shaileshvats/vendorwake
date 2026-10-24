import "server-only";

import { connection } from "next/server";

import { auth0 } from "@/lib/auth0";
import { getAuthConfigStatus } from "@/lib/auth-config";
import type { SessionUser } from "@/types/user";

/**
 * Returns the signed-in user, or null when signed out or when Auth0 is not configured.
 * Always opts the caller into request-time rendering, so a page built without Auth0
 * credentials is never served as a stale static signed-out page.
 */
export async function getCurrentUser(): Promise<SessionUser | null> {
  await connection();
  if (!getAuthConfigStatus().configured) return null;

  try {
    const session = await auth0.getSession();
    if (!session) return null;
    const { sub, name, email, picture } = session.user;
    return {
      sub,
      name: typeof name === "string" ? name : null,
      email: typeof email === "string" ? email : null,
      picture: typeof picture === "string" ? picture : null,
    };
  } catch (error) {
    console.error("Failed to read Auth0 session", error);
    return null;
  }
}
