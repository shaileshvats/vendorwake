"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type ReactNode } from "react";

import { CloseIcon, MenuIcon } from "@/components/icons";
import { Logo } from "@/components/logo";
import { SidebarNav } from "@/components/sidebar-nav";
import { UserMenu } from "@/components/user-menu";
import type { SessionUser } from "@/types/user";

const SIDEBAR_ID = "app-sidebar";

export function AppShell({ user, children }: { user: SessionUser; children: ReactNode }) {
  const [navOpen, setNavOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!navOpen) return;
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setNavOpen(false);
        toggleRef.current?.focus();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navOpen]);

  return (
    <div className="min-h-screen">
      <a
        href="#main"
        className="sr-only z-50 rounded-md bg-accent px-3 py-2 text-sm font-medium text-accent-fg focus:not-sr-only focus:fixed focus:left-3 focus:top-3"
      >
        Skip to content
      </a>

      {/* Mobile backdrop */}
      {navOpen ? (
        <div
          aria-hidden="true"
          className="fixed inset-0 z-30 bg-black/50 lg:hidden"
          onClick={() => setNavOpen(false)}
        />
      ) : null}

      <aside
        id={SIDEBAR_ID}
        aria-label="Sidebar"
        className={`fixed inset-y-0 left-0 z-40 w-60 flex-col border-r border-border bg-surface ${
          navOpen ? "flex" : "hidden"
        } lg:flex`}
      >
        <div className="flex h-14 items-center justify-between border-b border-border px-4">
          <Link href="/dashboard" className="rounded-md" onClick={() => setNavOpen(false)}>
            <Logo />
          </Link>
          <button
            type="button"
            onClick={() => {
              setNavOpen(false);
              toggleRef.current?.focus();
            }}
            className="rounded-md p-1.5 text-fg-muted hover:bg-surface-raised hover:text-fg lg:hidden"
          >
            <CloseIcon />
            <span className="sr-only">Close navigation</span>
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-3 py-4">
          <SidebarNav onNavigate={() => setNavOpen(false)} />
        </div>
        <div className="border-t border-border px-4 py-3 text-xs text-fg-subtle">Open source · upstream-to-impact</div>
      </aside>

      <div className="lg:pl-60">
        <header className="sticky top-0 z-20 flex h-14 items-center justify-between gap-3 border-b border-border bg-canvas/95 px-4 backdrop-blur-sm sm:px-6">
          <div className="flex items-center gap-2">
            <button
              ref={toggleRef}
              type="button"
              aria-expanded={navOpen}
              aria-controls={SIDEBAR_ID}
              onClick={() => setNavOpen((value) => !value)}
              className="rounded-md p-1.5 text-fg-muted hover:bg-surface-raised hover:text-fg lg:hidden"
            >
              <MenuIcon />
              <span className="sr-only">Open navigation</span>
            </button>
            <Link href="/dashboard" className="rounded-md lg:hidden">
              <Logo />
            </Link>
          </div>
          <UserMenu user={user} />
        </header>

        <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl px-4 py-6 focus:outline-none sm:px-6 lg:py-8">
          {children}
        </main>
      </div>
    </div>
  );
}
