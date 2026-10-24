import { getApiBaseUrl } from "@/lib/api";
import { getHealth, getMe } from "@/lib/endpoints";
import type { ApiError, HealthCheckValue } from "@/types/api";

type Tone = "ok" | "warn" | "error";

const TONE_CLASSES: Record<Tone, string> = {
  ok: "text-ok",
  warn: "text-impact-potential",
  error: "text-danger",
};

const TONE_LABELS: Record<Tone, string> = { ok: "OK", warn: "Degraded", error: "Error" };

function StatusDot({ tone }: { tone: Tone }) {
  // Shape differs per tone (filled circle / ring / square) so state is not color-only.
  return (
    <svg viewBox="0 0 10 10" width={10} height={10} aria-hidden="true" focusable="false" className={TONE_CLASSES[tone]}>
      {tone === "ok" ? <circle cx="5" cy="5" r="4" fill="currentColor" /> : null}
      {tone === "warn" ? <circle cx="5" cy="5" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.6" /> : null}
      {tone === "error" ? <rect x="1.5" y="1.5" width="7" height="7" fill="currentColor" /> : null}
    </svg>
  );
}

function Row({ label, tone, value, detail }: { label: string; tone: Tone; value: string; detail?: string }) {
  return (
    <div className="grid grid-cols-[1fr_auto] items-center gap-x-3 py-2">
      <dt className="text-sm text-fg-muted">{label}</dt>
      <dd className={`inline-flex items-center gap-1.5 text-sm font-medium ${TONE_CLASSES[tone]}`}>
        <StatusDot tone={tone} />
        <span className="sr-only">{TONE_LABELS[tone]}: </span>
        {value}
      </dd>
      {detail ? <dd className="col-span-2 mt-0.5 break-words text-xs text-fg-subtle">{detail}</dd> : null}
    </div>
  );
}

const HEALTHY = new Set(["ok", "healthy", "up", "pass", "passing"]);

function checkValueText(value: HealthCheckValue | undefined): string {
  if (value === undefined) return "not reported";
  if (typeof value === "string") return value;
  if (typeof value.status === "string") return value.status;
  return "reported";
}

function toneFor(value: string): Tone {
  return HEALTHY.has(value.toLowerCase()) ? "ok" : "warn";
}

/** Short, actionable explanation for each failure mode. */
function errorDetail(error: ApiError): string {
  switch (error.kind) {
    case "auth_not_configured":
      return "Set the AUTH0_* variables on the frontend server to enable sign-in.";
    case "audience_not_configured":
      return "Set AUTH0_AUDIENCE to the Vendorwake API identifier, then sign in again.";
    case "no_session":
      return "Sign in to call authenticated endpoints.";
    case "token_error":
      return `${error.message} Signing out and in again usually fixes this.`;
    case "unauthorized":
      return "The API rejected the access token. Check that the API's Auth0 domain and audience match the frontend's.";
    case "forbidden":
      return "The token is valid but lacks permission for this endpoint.";
    default:
      return error.message;
  }
}

function errorValue(error: ApiError): string {
  switch (error.kind) {
    case "auth_not_configured":
    case "audience_not_configured":
      return "Not configured";
    case "no_session":
      return "Signed out";
    case "unreachable":
      return "Unreachable";
    case "timeout":
      return "Timed out";
    case "unauthorized":
      return "Unauthorized";
    case "forbidden":
      return "Forbidden";
    default:
      return error.status ? `HTTP ${error.status}` : "Error";
  }
}

export async function SystemStatus() {
  const [health, me] = await Promise.all([getHealth(), getMe()]);
  const apiBase = getApiBaseUrl();

  return (
    <div>
      <dl className="divide-y divide-border">
        {health.ok ? (
          <>
            <Row
              label="API"
              tone={toneFor(health.data.status)}
              value={health.data.status}
              detail={`version ${health.data.version}`}
            />
            <Row
              label="Database"
              tone={toneFor(checkValueText(health.data.checks.database))}
              value={checkValueText(health.data.checks.database)}
            />
            <Row
              label="Temporal"
              tone={toneFor(checkValueText(health.data.checks.temporal))}
              value={checkValueText(health.data.checks.temporal)}
            />
          </>
        ) : (
          <Row label="API" tone="error" value={errorValue(health.error)} detail={errorDetail(health.error)} />
        )}

        {me.ok ? (
          <Row
            label="API authentication"
            tone="ok"
            value="Accepted"
            detail={`${me.data.email ?? me.data.subject}${
              me.data.scopes.length ? ` · scopes: ${me.data.scopes.join(" ")}` : " · no scopes granted"
            }`}
          />
        ) : (
          <Row
            label="API authentication"
            tone={me.error.kind === "unreachable" || me.error.kind === "timeout" ? "error" : "warn"}
            value={errorValue(me.error)}
            detail={errorDetail(me.error)}
          />
        )}
      </dl>
      <p className="mt-3 border-t border-border pt-3 font-mono text-[11px] text-fg-subtle">
        GET {apiBase}/api/health · GET {apiBase}/api/me
      </p>
    </div>
  );
}

export function SystemStatusFallback() {
  return (
    <p className="py-2 text-sm text-fg-muted" role="status">
      Checking API status…
    </p>
  );
}
