import type { ReactNode } from "react";

export type ImpactStatus = "confirmed" | "potential" | "unaffected" | "unknown";

interface StatusStyle {
  label: string;
  description: string;
  className: string;
  icon: ReactNode;
}

/*
 * Status is never conveyed by color alone: each status has a distinct icon shape
 * (filled diamond, hollow triangle, check, question mark) and a text label. Hues follow
 * the colorblind-safe Okabe-Ito palette (vermillion / amber / blue / gray), avoiding a
 * red-green pairing.
 */
const STATUS_STYLES: Record<ImpactStatus, StatusStyle> = {
  confirmed: {
    label: "Confirmed",
    description: "Evidence shows the upstream change affects this code.",
    className: "text-impact-confirmed bg-impact-confirmed/12 border-impact-confirmed/45",
    icon: <path d="M8 2.5 13.5 8 8 13.5 2.5 8Z" fill="currentColor" />,
  },
  potential: {
    label: "Potential",
    description: "The change may affect this code; not yet verified.",
    className: "text-impact-potential bg-impact-potential/12 border-impact-potential/45",
    icon: <path d="M8 3 13.5 12.5H2.5Z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />,
  },
  unaffected: {
    label: "Unaffected",
    description: "Evidence shows this code is not affected.",
    className: "text-impact-unaffected bg-impact-unaffected/12 border-impact-unaffected/45",
    icon: (
      <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
    ),
  },
  unknown: {
    label: "Unknown",
    description: "Not enough evidence to decide.",
    className: "text-impact-unknown bg-impact-unknown/10 border-impact-unknown/40 border-dashed",
    icon: (
      <>
        <path
          d="M6 6.2a2 2 0 1 1 2.8 1.8c-.5.3-.8.7-.8 1.3v.4"
          fill="none"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <circle cx="8" cy="12.3" r="0.9" fill="currentColor" />
      </>
    ),
  },
};

export const IMPACT_STATUSES = Object.keys(STATUS_STYLES) as ImpactStatus[];

export function impactStatusDescription(status: ImpactStatus): string {
  return STATUS_STYLES[status].description;
}

export function ImpactStatusBadge({ status, size = "md" }: { status: ImpactStatus; size?: "sm" | "md" }) {
  const style = STATUS_STYLES[status];
  const sizing = size === "sm" ? "gap-1 px-1.5 py-0.5 text-[11px]" : "gap-1.5 px-2 py-0.5 text-xs";
  return (
    <span
      className={`inline-flex items-center whitespace-nowrap rounded-full border font-medium ${sizing} ${style.className}`}
      title={style.description}
    >
      <svg viewBox="0 0 16 16" width={12} height={12} aria-hidden="true" focusable="false">
        {style.icon}
      </svg>
      <span>
        <span className="sr-only">Impact: </span>
        {style.label}
      </span>
    </span>
  );
}
