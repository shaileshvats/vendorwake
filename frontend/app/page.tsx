import { redirect } from "next/navigation";

import { Logo } from "@/components/logo";
import { getAuthConfigStatus } from "@/lib/auth-config";
import { getCurrentUser } from "@/lib/session";

export default async function LandingPage() {
  const user = await getCurrentUser();
  if (user) redirect("/dashboard");

  const authConfig = getAuthConfigStatus();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between px-4 sm:px-6">
          <Logo />
          {authConfig.configured ? (
            <a
              href="/auth/login"
              className="rounded-md px-3 py-1.5 text-sm font-medium text-fg-muted hover:bg-surface-raised hover:text-fg"
            >
              Sign in
            </a>
          ) : null}
        </div>
      </header>

      <main id="main" className="mx-auto flex w-full max-w-5xl flex-1 flex-col justify-center px-4 py-16 sm:px-6">
        <h1 className="max-w-2xl text-3xl font-semibold tracking-tight text-fg sm:text-4xl">
          Know which upstream changes reach your code.
        </h1>
        <p className="mt-4 max-w-2xl text-base leading-relaxed text-fg-muted">
          Vendorwake monitors vendor changelogs and API docs, maps each change to the code in your
          repositories that depends on it, and produces evidence-backed investigations that mark every
          integration as confirmed, potentially affected, or unaffected.
        </p>

        <div className="mt-8">
          {authConfig.configured ? (
            // Plain anchor: the SDK login route must not be prefetched.
            <a
              href="/auth/login"
              className="inline-flex items-center rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-strong"
            >
              Sign in
            </a>
          ) : (
            <div role="status" className="max-w-xl rounded-lg border border-border bg-surface p-4">
              <p className="text-sm font-medium text-fg">Sign-in is not available on this server</p>
              <p className="mt-1 text-sm text-fg-muted">
                Auth0 is not configured. Set the following environment variables and restart the
                frontend:
              </p>
              <ul className="mt-2 flex flex-wrap gap-1.5">
                {authConfig.missing.map((name) => (
                  <li key={name}>
                    <code className="rounded border border-border bg-surface-raised px-1.5 py-0.5 font-mono text-xs text-fg">
                      {name}
                    </code>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </main>

      <footer className="border-t border-border">
        <p className="mx-auto w-full max-w-5xl px-4 py-4 text-xs text-fg-subtle sm:px-6">
          Vendorwake is open source. Upstream-to-impact dependency intelligence.
        </p>
      </footer>
    </div>
  );
}
