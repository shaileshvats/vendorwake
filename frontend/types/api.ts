/** Response of `GET /api/health` (unauthenticated). */
export interface HealthResponse {
  status: string;
  version: string;
  checks: {
    database: HealthCheckValue;
    temporal: HealthCheckValue;
    [name: string]: HealthCheckValue;
  };
}

/** A dependency check result. Typically a short status string such as "ok". */
export type HealthCheckValue = string | { status?: string; [key: string]: unknown };

/** Response of `GET /api/me` (requires a Bearer access token). */
export interface MeResponse {
  subject: string;
  email: string | null;
  scopes: string[];
}

export type ApiErrorKind =
  /** Auth0 credentials are not set on the frontend server. */
  | "auth_not_configured"
  /** AUTH0_AUDIENCE is not set, so no API access token can be requested. */
  | "audience_not_configured"
  /** No signed-in session to take a token from. */
  | "no_session"
  /** The SDK could not provide an access token (e.g. refresh failed). */
  | "token_error"
  /** Connection refused, DNS failure, or other network error. */
  | "unreachable"
  | "timeout"
  | "unauthorized"
  | "forbidden"
  | "not_found"
  | "server_error"
  | "http_error"
  /** The response body was not the expected JSON shape. */
  | "invalid_response";

export interface ApiError {
  kind: ApiErrorKind;
  message: string;
  /** HTTP status, when a response was received. */
  status?: number;
  /** Parsed JSON body of a non-2xx response, when available. */
  body?: unknown;
}

export type ApiResult<T> = { ok: true; data: T; status: number } | { ok: false; error: ApiError };
