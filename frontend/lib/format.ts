/** Formatting helpers shared by server and client components. Deterministic (UTC) to avoid hydration drift. */

const DATE_TIME = new Intl.DateTimeFormat("en-GB", {
  timeZone: "UTC",
  year: "numeric",
  month: "short",
  day: "2-digit",
  hour: "2-digit",
  minute: "2-digit",
  hour12: false,
});

const DATE = new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", year: "numeric", month: "short", day: "2-digit" });

function parse(value: string | null | undefined): Date | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value: string | null | undefined): string {
  const date = parse(value);
  return date ? `${DATE_TIME.format(date)} UTC` : "—";
}

export function formatDate(value: string | null | undefined): string {
  const date = parse(value);
  return date ? DATE.format(date) : "—";
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return `${Math.round(value * 100)}%`;
}

export function formatSeconds(value: number | null | undefined): string {
  if (value === null || value === undefined) return "—";
  return value < 1 ? `${Math.round(value * 1000)} ms` : `${value.toFixed(value < 10 ? 2 : 1)} s`;
}

export function formatNumber(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : value.toLocaleString("en-US");
}

export function shortSha(value: string | null | undefined, length = 12): string {
  return value ? value.slice(0, length) : "—";
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ");
}

/** Only http(s) URLs are rendered as links; anything else is shown as text. */
export function safeHref(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:" ? url.toString() : null;
  } catch {
    return null;
  }
}

export function hostOf(value: string): string {
  try {
    return new URL(value).host;
  } catch {
    return value;
  }
}

/**
 * GitHub blob link for a file reported by repository analysis. Paths are treated as relative to the
 * analysed subpath unless they already start with it (the contract does not pin this down).
 */
export function githubFileUrl(
  repo: { html_url: string; ref: string; subpath: string; commit_sha: string | null },
  filePath: string,
  line?: number | null,
): string | null {
  const base = safeHref(repo.html_url);
  if (!base) return null;
  const subpath = repo.subpath.replace(/^\/+|\/+$/g, "");
  const clean = filePath.replace(/^\/+/, "");
  const full = subpath && !clean.startsWith(`${subpath}/`) ? `${subpath}/${clean}` : clean;
  const rev = repo.commit_sha || repo.ref || "HEAD";
  const encode = (path: string) => path.split("/").map(encodeURIComponent).join("/");
  return `${base.replace(/\/+$/, "")}/blob/${encode(rev)}/${encode(full)}${line ? `#L${line}` : ""}`;
}
