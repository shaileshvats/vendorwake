import Link from "next/link";
import type { ReactNode } from "react";

import { formatDateTime, safeHref } from "@/lib/format";
import type { Severity } from "@/types/contracts";

/* Small presentational building blocks shared by pages. Server- and client-safe (no hooks). */

export const buttonClass = {
  primary:
    "inline-flex items-center justify-center gap-1.5 rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-accent-fg hover:bg-accent-strong disabled:cursor-not-allowed disabled:opacity-50",
  secondary:
    "inline-flex items-center justify-center gap-1.5 rounded-md border border-border-strong bg-surface-raised px-3 py-1.5 text-sm font-medium text-fg hover:border-fg-subtle disabled:cursor-not-allowed disabled:opacity-50",
  ghost:
    "inline-flex items-center justify-center gap-1.5 rounded-md px-2 py-1 text-sm text-fg-muted hover:bg-surface-raised hover:text-fg disabled:cursor-not-allowed disabled:opacity-50",
  danger:
    "inline-flex items-center justify-center gap-1.5 rounded-md border border-danger/50 px-3 py-1.5 text-sm font-medium text-danger hover:bg-danger/10 disabled:cursor-not-allowed disabled:opacity-50",
} as const;

export const inputClass =
  "w-full rounded-md border border-border-strong bg-canvas px-2.5 py-1.5 text-sm text-fg placeholder:text-fg-subtle focus:border-accent";

export const linkClass = "text-accent underline-offset-2 hover:text-accent-strong hover:underline";

export const tableClass = "w-full border-collapse text-left text-sm";
export const thClass = "border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wide text-fg-subtle";
export const tdClass = "border-b border-border px-3 py-2 align-top";

export type Tone = "neutral" | "info" | "warn" | "danger" | "ok";

const TONE: Record<Tone, string> = {
  neutral: "border-border-strong text-fg-muted",
  info: "border-accent/50 text-accent",
  warn: "border-impact-potential/50 text-impact-potential",
  danger: "border-impact-confirmed/55 text-impact-confirmed",
  ok: "border-impact-unaffected/50 text-impact-unaffected",
};

/** Bordered text pill. Meaning always comes from the text; color only reinforces it. */
export function Pill({ tone = "neutral", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex max-w-full items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

const SEVERITY_LEVEL: Record<Severity, number> = { critical: 4, high: 3, medium: 2, low: 1, info: 0 };
const SEVERITY_TONE: Record<Severity, Tone> = {
  critical: "danger",
  high: "danger",
  medium: "warn",
  low: "neutral",
  info: "neutral",
};

/** Severity with a 4-step bar glyph (shape encodes level) and a text label. */
export function SeverityBadge({ severity }: { severity: Severity | string | null | undefined }) {
  if (!severity) return <span className="text-fg-subtle">—</span>;
  const known = severity in SEVERITY_LEVEL ? (severity as Severity) : null;
  const level = known ? SEVERITY_LEVEL[known] : 0;
  return (
    <Pill tone={known ? SEVERITY_TONE[known] : "neutral"}>
      <svg viewBox="0 0 16 10" width={14} height={9} aria-hidden="true" focusable="false">
        {[0, 1, 2, 3].map((i) => (
          <rect
            key={i}
            x={i * 4}
            y={8 - (i + 1) * 2}
            width={3}
            height={(i + 1) * 2}
            fill={i < level ? "currentColor" : "none"}
            stroke="currentColor"
            strokeWidth={0.8}
          />
        ))}
      </svg>
      <span>
        <span className="sr-only">Severity: </span>
        {severity}
      </span>
    </Pill>
  );
}

export function OfficialBadge({ official }: { official: boolean }) {
  return official ? (
    <Pill tone="ok" title="The URL host is one of the vendor's official domains.">
      <svg viewBox="0 0 16 16" width={10} height={10} aria-hidden="true" focusable="false">
        <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
      </svg>
      Official source
    </Pill>
  ) : (
    <Pill tone="warn" title="The URL host is not one of the vendor's official domains.">
      Unofficial source
    </Pill>
  );
}

export function Time({ value }: { value: string | null | undefined }) {
  if (!value) return <span className="text-fg-subtle">—</span>;
  return (
    <time dateTime={value} className="tabular-nums">
      {formatDateTime(value)}
    </time>
  );
}

export function Mono({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <code className={`break-all font-mono text-xs text-fg ${className}`}>{children}</code>;
}

/** External link that opens in a new tab; renders plain text for non-http(s) URLs. */
export function ExternalLink({ href, children, className = "" }: { href: string | null | undefined; children: ReactNode; className?: string }) {
  const safe = safeHref(href);
  if (!safe) return <span className={className}>{children}</span>;
  return (
    <a href={safe} target="_blank" rel="noopener noreferrer" className={`${linkClass} ${className}`}>
      {children}
      <span className="sr-only"> (opens in a new tab)</span>
      <svg viewBox="0 0 12 12" width={10} height={10} aria-hidden="true" focusable="false" className="ml-0.5 inline-block align-baseline">
        <path d="M4.5 2.5h5v5M9.5 2.5 3 9" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
      </svg>
    </a>
  );
}

export function KeyValueList({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className="divide-y divide-border text-sm">
      {items.map((item) => (
        // Wraps the value under its label when the container is too narrow for both.
        <div key={item.label} className="flex flex-wrap gap-x-3 gap-y-0.5 py-2">
          <dt className="w-28 shrink-0 text-fg-muted">{item.label}</dt>
          <dd className="min-w-[10rem] flex-1 break-words text-fg">{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** A count tile for the dashboard. */
export function StatTile({ label, value, hint, href }: { label: string; value: ReactNode; hint?: string; href?: string }) {
  const body = (
    <>
      <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{label}</p>
      <p className="mt-1 text-2xl font-semibold tabular-nums text-fg">{value}</p>
      {hint ? <p className="mt-0.5 text-xs text-fg-muted">{hint}</p> : null}
    </>
  );
  return href ? (
    <Link href={href} className="block rounded-lg border border-border bg-surface p-4 hover:border-border-strong">
      {body}
    </Link>
  ) : (
    <div className="rounded-lg border border-border bg-surface p-4">{body}</div>
  );
}

export function CodeBlock({ children, label }: { children: string; label?: string }) {
  return (
    <pre
      aria-label={label}
      tabIndex={0}
      className="max-h-80 overflow-auto rounded-md border border-border bg-canvas p-3 font-mono text-xs leading-relaxed text-fg"
    >
      {children}
    </pre>
  );
}

export function Callout({ tone = "info", title, children }: { tone?: Tone; title: string; children?: ReactNode }) {
  const border: Record<Tone, string> = {
    neutral: "border-l-border-strong",
    info: "border-l-accent",
    warn: "border-l-impact-potential",
    danger: "border-l-impact-confirmed",
    ok: "border-l-impact-unaffected",
  };
  return (
    <div className={`rounded-md border border-border border-l-4 ${border[tone]} bg-surface px-4 py-3`}>
      <p className="text-sm font-medium text-fg">{title}</p>
      {children ? <div className="mt-1 text-sm text-fg-muted">{children}</div> : null}
    </div>
  );
}
