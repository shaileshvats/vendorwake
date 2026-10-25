"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

/**
 * Re-renders the current server page on an interval while `active` (e.g. analysis or an
 * investigation is running). Pauses while the tab is hidden and stops after `maxMinutes`.
 */
export function AutoRefresh({
  active,
  intervalMs = 4_000,
  maxMinutes = 15,
  label = "Live",
}: {
  active: boolean;
  intervalMs?: number;
  maxMinutes?: number;
  label?: string;
}) {
  const router = useRouter();
  const [expired, setExpired] = useState(false);

  useEffect(() => {
    if (!active) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => {
      if (Date.now() - startedAt > maxMinutes * 60_000) {
        window.clearInterval(timer);
        setExpired(true);
        return;
      }
      if (document.visibilityState === "visible") router.refresh();
    }, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs, maxMinutes, router]);

  if (!active) return null;
  return (
    <span className="inline-flex items-center gap-1.5 text-xs text-fg-muted">
      <svg viewBox="0 0 10 10" width={8} height={8} aria-hidden="true" focusable="false" className="text-accent">
        <circle cx="5" cy="5" r="3.2" fill="none" stroke="currentColor" strokeWidth="1.6" />
      </svg>
      {expired ? (
        <>
          Auto-refresh stopped.{" "}
          <button type="button" className="text-accent hover:text-accent-strong" onClick={() => router.refresh()}>
            Refresh now
          </button>
        </>
      ) : (
        `${label} · refreshing every ${Math.round(intervalMs / 1000)} s`
      )}
    </span>
  );
}
