import "server-only";

import { apiRequest } from "@/lib/api";
import type { ApiResult, HealthResponse, MeResponse } from "@/types/api";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isHealthResponse(value: unknown): value is HealthResponse {
  return (
    isRecord(value) &&
    typeof value.status === "string" &&
    typeof value.version === "string" &&
    isRecord(value.checks)
  );
}

export function isMeResponse(value: unknown): value is MeResponse {
  return (
    isRecord(value) &&
    typeof value.subject === "string" &&
    (value.email === null || value.email === undefined || typeof value.email === "string") &&
    Array.isArray(value.scopes) &&
    value.scopes.every((scope) => typeof scope === "string")
  );
}

/** `GET /api/health`. A degraded API may answer non-2xx with a valid body; that body is kept. */
export async function getHealth(): Promise<ApiResult<HealthResponse>> {
  const result = await apiRequest<HealthResponse>("/api/health", { validate: isHealthResponse, timeoutMs: 4_000 });
  if (!result.ok && result.error.status && isHealthResponse(result.error.body)) {
    return { ok: true, data: result.error.body, status: result.error.status };
  }
  return result;
}

/** `GET /api/me` with the user's access token. */
export function getMe(): Promise<ApiResult<MeResponse>> {
  return apiRequest<MeResponse>("/api/me", { auth: true, validate: isMeResponse, timeoutMs: 4_000 });
}
