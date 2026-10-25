import type { ApiError } from "@/types/api";

interface ErrorCopy {
  title: string;
  hint: string;
  signIn?: boolean;
}

/** One message per failure mode so 401, 403, 404 and 503 read differently. */
export function describeApiError(error: Pick<ApiError, "kind" | "message" | "status">, resource = "this data"): ErrorCopy {
  switch (error.kind) {
    case "unauthorized":
      return {
        title: "Your session is no longer accepted by the API (401).",
        hint: "Sign in again. If this persists, the API's Auth0 domain or audience does not match the frontend's.",
        signIn: true,
      };
    case "no_session":
      return { title: "You are signed out.", hint: "Sign in to load this page.", signIn: true };
    case "token_error":
      return { title: "Could not obtain an API access token.", hint: `${error.message} Signing out and in again usually fixes this.`, signIn: true };
    case "forbidden":
      return {
        title: "You do not have permission to view this (403).",
        hint: "Your token is valid but lacks the scope this endpoint requires.",
      };
    case "not_found":
      return { title: `Not found (404).`, hint: `${resource[0].toUpperCase()}${resource.slice(1)} does not exist or belongs to another account.` };
    case "service_unavailable":
      return {
        title: "A backend dependency is unavailable (503).",
        hint: `${error.message}. The database, Temporal or a sponsor integration may be down or not configured.`,
      };
    case "unreachable":
      return { title: "The Vendorwake API is unreachable.", hint: error.message };
    case "timeout":
      return { title: "The API timed out.", hint: error.message };
    case "invalid_response":
      return { title: "The API returned an unexpected response.", hint: "The response did not match the API contract the UI expects." };
    case "auth_not_configured":
    case "audience_not_configured":
      return { title: "Authentication is not configured.", hint: error.message };
    default:
      return { title: error.status ? `The API returned an error (HTTP ${error.status}).` : "Request failed.", hint: error.message };
  }
}

export function ApiErrorState({ error, resource }: { error: Pick<ApiError, "kind" | "message" | "status">; resource?: string }) {
  const copy = describeApiError(error, resource);
  return (
    <div role="alert" className="rounded-md border border-danger/40 bg-danger/5 px-4 py-3">
      <p className="text-sm font-medium text-fg">
        <span aria-hidden="true" className="mr-1.5 inline-block size-2 bg-danger align-middle" />
        {copy.title}
      </p>
      <p className="mt-1 text-xs text-fg-muted">{copy.hint}</p>
      {copy.signIn ? (
        // Plain anchor so the SDK route is not prefetched.
        <a href="/auth/login" className="mt-2 inline-block text-xs font-medium text-accent hover:text-accent-strong">
          Sign in again
        </a>
      ) : null}
    </div>
  );
}

/** Inline (single-line) variant for action results. */
export function InlineError({ error }: { error: Pick<ApiError, "kind" | "message" | "status"> }) {
  const copy = describeApiError(error);
  return (
    <p role="alert" className="text-xs text-danger">
      {copy.title} <span className="text-fg-muted">{copy.hint}</span>
    </p>
  );
}
