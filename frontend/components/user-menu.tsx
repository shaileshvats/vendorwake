"use client";

import { useEffect, useId, useRef, useState } from "react";

import { ChevronDownIcon } from "@/components/icons";
import type { SessionUser } from "@/types/user";

function initials(user: SessionUser): string {
  const source = user.name || user.email || "?";
  const parts = source.replace(/@.*/, "").split(/[\s._-]+/).filter(Boolean);
  return (parts.length > 1 ? parts[0][0] + parts[1][0] : source.slice(0, 2)).toUpperCase();
}

export function UserMenu({ user }: { user: SessionUser }) {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const panelId = useId();
  const displayName = user.name || user.email || "Signed in";

  useEffect(() => {
    if (!open) return;
    function onPointerDown(event: PointerEvent) {
      if (!containerRef.current?.contains(event.target as Node)) setOpen(false);
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        buttonRef.current?.focus();
      }
    }
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  return (
    <div ref={containerRef} className="relative">
      <button
        ref={buttonRef}
        type="button"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((value) => !value)}
        className="flex items-center gap-2 rounded-md px-1.5 py-1 text-sm text-fg-muted hover:bg-surface-raised hover:text-fg"
      >
        <span
          aria-hidden="true"
          className="flex size-7 items-center justify-center rounded-full border border-border bg-surface-raised text-xs font-medium text-fg"
        >
          {initials(user)}
        </span>
        <span className="hidden max-w-48 truncate sm:inline">{displayName}</span>
        <span className="sr-only sm:hidden">Account menu for {displayName}</span>
        <ChevronDownIcon className="text-fg-subtle" />
      </button>

      <div
        id={panelId}
        hidden={!open}
        className="absolute right-0 z-30 mt-2 w-64 rounded-lg border border-border bg-surface p-1 shadow-lg shadow-black/20"
      >
        <div className="border-b border-border px-3 py-2.5">
          {user.name ? <p className="truncate text-sm font-medium text-fg">{user.name}</p> : null}
          {user.email ? <p className="truncate text-xs text-fg-muted">{user.email}</p> : null}
          {!user.name && !user.email ? <p className="truncate font-mono text-xs text-fg-muted">{user.sub}</p> : null}
        </div>
        {/* A plain anchor (not next/link) so the SDK logout route is never prefetched. */}
        <a
          href="/auth/logout"
          className="mt-1 block rounded-md px-3 py-2 text-sm text-fg hover:bg-surface-raised"
        >
          Sign out
        </a>
      </div>
    </div>
  );
}
