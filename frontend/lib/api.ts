import "server-only";

import { auth0 } from "@/lib/auth0";
import { getAuthConfigStatus } from "@/lib/auth-config";
import type { ApiError, ApiErrorKind, ApiResult } from "@/types/api";

const DEFAULT_TIMEOUT_MS = 5_000;

/**
 * Base URL of the Vendorwake API for server-side requests.
 *
 * `API_BASE_URL` is read at runtime and is meant for container networking
 * (e.g. http://api:8000). `NEXT_PUBLIC_API_BASE_URL` is inlined at build time.
 */
export function getApiBaseUrl(): string {
  const raw =
    process.env.API_BASE_URL?.trim() ||
    process.env.NEXT_PUBLIC_API_BASE_URL?.trim() ||
    "http://localhost:8000";
  return raw.replace(/\/+$/, "");
}

export interface ApiRequestOptions {
  /** Attach the signed-in user's Auth0 access token as a Bearer token. */
  auth?: boolean;
  timeoutMs?: number;
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  body?: unknown;
  /** Runtime check that the JSON body has the expected shape. */
  validate?: (data: unknown) => boolean;
}

function fail(kind: ApiErrorKind, message: string, extra?: Partial<ApiError>): { ok: false; error: ApiError } {
  return { ok: false, error: { kind, message, ...extra } };
}

function kindForStatus(status: number): ApiErrorKind {
  if (status === 401) return "unauthorized";
  if (status === 403) return "forbidden";
  if (status === 404) return "not_found";
  if (status === 409) return "conflict";
  if (status === 422) return "validation_error";
  if (status === 503) return "service_unavailable";
  if (status >= 500) return "server_error";
  return "http_error";
}

async function getBearerToken(): Promise<{ ok: true; token: string } | { ok: false; error: ApiError }> {
  const authConfig = getAuthConfigStatus();
  if (!authConfig.configured) {
    return fail("auth_not_configured", "Auth0 is not configured on the frontend server.");
  }
  if (!authConfig.audienceConfigured) {
    return fail("audience_not_configured", "AUTH0_AUDIENCE is not set, so no API access token can be requested.");
  }
  try {
    const { token } = await auth0.getAccessToken();
    return { ok: true, token };
  } catch (error) {
    const code = typeof error === "object" && error && "code" in error ? String(error.code) : "unknown";
    if (code === "missing_session") return fail("no_session", "You are not signed in.");
    return fail("token_error", `Could not obtain an access token (${code}).`);
  }
}

/** Reads `{"error": {"code", "message"}}` (the API's error shape) or a FastAPI `{"detail": str}`. */
function parseErrorBody(data: unknown): { code?: string; message?: string } {
  if (!data || typeof data !== "object") return {};
  if ("error" in data && data.error && typeof data.error === "object") {
    const error = data.error as Record<string, unknown>;
    return {
      code: typeof error.code === "string" ? error.code : undefined,
      message: typeof error.message === "string" ? error.message : undefined,
    };
  }
  if ("detail" in data && typeof data.detail === "string") return { message: data.detail };
  return {};
}

/**
 * Typed server-side fetch against the Vendorwake API. Never throws: every failure is
 * mapped to an `ApiError` so pages can render a specific error state.
 */
export async function apiRequest<T>(path: string, options: ApiRequestOptions = {}): Promise<ApiResult<T>> {
  const { auth = false, timeoutMs = DEFAULT_TIMEOUT_MS, method = "GET", body, validate } = options;
  const url = `${getApiBaseUrl()}${path.startsWith("/") ? path : `/${path}`}`;

  const headers = new Headers({ Accept: "application/json" });
  if (body !== undefined) headers.set("Content-Type", "application/json");
  if (auth) {
    const token = await getBearerToken();
    if (!token.ok) return token;
    headers.set("Authorization", `Bearer ${token.token}`);
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
      signal: AbortSignal.timeout(timeoutMs),
    });
  } catch (error) {
    if (error instanceof DOMException && (error.name === "TimeoutError" || error.name === "AbortError")) {
      return fail("timeout", `The API did not respond within ${timeoutMs / 1000}s.`);
    }
    return fail("unreachable", `Could not connect to the API at ${getApiBaseUrl()}.`);
  }

  let data: unknown = undefined;
  const text = await response.text().catch(() => "");
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = undefined;
    }
  }

  if (!response.ok) {
    const { code, message } = parseErrorBody(data);
    const detail = message ? `: ${message}` : "";
    return fail(kindForStatus(response.status), `The API responded with HTTP ${response.status}${detail}`, {
      status: response.status,
      code,
      body: data,
    });
  }

  if (data === undefined && response.status === 204) {
    return { ok: true, data: undefined as T, status: response.status };
  }
  if (data === undefined || (validate && !validate(data))) {
    return fail("invalid_response", "The API returned an unexpected response.", { status: response.status });
  }

  return { ok: true, data: data as T, status: response.status };
}
