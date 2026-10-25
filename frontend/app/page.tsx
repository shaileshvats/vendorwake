import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { ImpactStatusBadge, type ImpactStatus } from "@/components/impact-status-badge";
import { Logo } from "@/components/logo";
import { getAuthConfigStatus } from "@/lib/auth-config";
import { getCurrentUser } from "@/lib/session";

const REPO_URL = "https://github.com/shaileshvats/vendorwake";

const STEPS: { title: string; body: string; detail: string }[] = [
  {
    title: "Watch upstream",
    body: "Searches for vendor announcements, then reads only the vendor's official sources.",
    detail: "SerpApi discovery · Browserbase for JS-rendered docs · every fetch hashed into a TimescaleDB snapshot",
  },
  {
    title: "Extract what changed",
    body: "An open-weight model turns release notes into renames, removals and deprecations, each with a verbatim quote.",
    detail: "Gemma 4 · quotes that are not in the source are flagged, never trusted",
  },
  {
    title: "Map it to your code",
    body: "Static analysis finds the exact files, lines and calls that use each affected symbol and version.",
    detail: "Manifests + Python AST · file:line evidence · no model guesses here",
  },
  {
    title: "Prove it",
    body: "Runs your tests on the old and new version in a sandbox, proposes patches, and runs the tests again.",
    detail: "Temporal workflows · patches are proposed, never pushed",
  },
];

const VERDICTS: { status: ImpactStatus; rule: string }[] = [
  { status: "confirmed", rule: "Deterministic proof: an exact reference to a removed or renamed API, or a test that fails on the new version." },
  { status: "potential", rule: "A plausible connection the model found. Shown, but it waits for proof." },
  { status: "unaffected", rule: "The vendor or the affected symbols are not used." },
];

const RUN_ROWS: { where: string; what: string; status: ImpactStatus }[] = [
  { where: "billing/invoices.py:10", what: "invoice.request(...): removed in 16.0.0", status: "confirmed" },
  { where: "billing/payouts.py:6", what: "stripe.Reversal: renamed to stripe.TransferReversal", status: "confirmed" },
  { where: "billing/customers.py:8", what: "customers.retrieve(customer=...): path params now positional-only", status: "confirmed" },
  { where: "billing/catalog.py", what: "uses Stripe, but none of the changed APIs", status: "unaffected" },
];

const STACK: { name: string; role: string }[] = [
  { name: "Gemma 4", role: "open-weight model for extraction, impact rationale, patches and follow-ups" },
  { name: "Temporal", role: "durable workflows; each step is persisted, so retries never redo finished work" },
  { name: "Tiger Data", role: "Postgres + TimescaleDB snapshots, pgvector, row-level security per user" },
  { name: "Auth0", role: "sign-in, plus one scoped identity per agent; no agent can push code" },
  { name: "Backboard", role: "investigation memory for follow-up questions, with the memories shown" },
  { name: "Browserbase", role: "real browser sessions for docs that need JavaScript" },
  { name: "SerpApi", role: "discovery, always labelled as discovery, not proof" },
  { name: "Sentry", role: "agent tracing: every model call and tool step as spans" },
  { name: "Entire", role: "the migration agent's session, saved as a checkpoint linked to its commit" },
];

function SignInButton({ configured, children }: { configured: boolean; children: ReactNode }) {
  if (!configured) return null;
  // Plain anchor: the SDK login route must not be prefetched.
  return (
    <a
      href="/auth/login"
      className="inline-flex items-center rounded-md bg-accent px-4 py-2 text-sm font-medium text-accent-fg hover:bg-accent-strong"
    >
      {children}
    </a>
  );
}

function SectionHeading({ eyebrow, title, intro }: { eyebrow: string; title: string; intro?: string }) {
  return (
    <div className="max-w-2xl">
      <p className="font-mono text-xs uppercase tracking-wider text-fg-subtle">{eyebrow}</p>
      <h2 className="mt-2 text-xl font-semibold tracking-tight text-fg sm:text-2xl">{title}</h2>
      {intro ? <p className="mt-2 text-sm leading-relaxed text-fg-muted">{intro}</p> : null}
    </div>
  );
}

export default async function LandingPage() {
  const user = await getCurrentUser();
  if (user) redirect("/dashboard");

  const authConfig = getAuthConfigStatus();

  return (
    <div className="flex min-h-screen flex-col">
      <header className="border-b border-border">
        <div className="mx-auto flex h-14 w-full max-w-5xl items-center justify-between px-4 sm:px-6">
          <Logo />
          <nav aria-label="Primary" className="flex items-center gap-1">
            <a
              href={REPO_URL}
              className="rounded-md px-3 py-1.5 text-sm text-fg-muted hover:bg-surface-raised hover:text-fg"
            >
              GitHub
            </a>
            {authConfig.configured ? (
              <a
                href="/auth/login"
                className="rounded-md px-3 py-1.5 text-sm font-medium text-fg-muted hover:bg-surface-raised hover:text-fg"
              >
                Sign in
              </a>
            ) : null}
          </nav>
        </div>
      </header>

      <main id="main" className="mx-auto w-full max-w-5xl flex-1 px-4 sm:px-6">
        {/* Hero */}
        <section className="py-16 sm:py-20">
          <p className="font-mono text-xs uppercase tracking-wider text-accent">Upstream-to-impact intelligence</p>
          <h1 className="mt-3 max-w-3xl text-3xl font-semibold tracking-tight text-fg sm:text-5xl sm:leading-[1.1]">
            The upstream changed. Vendorwake already checked your code.
          </h1>
          <p className="mt-5 max-w-2xl text-base leading-relaxed text-fg-muted">
            Vendorwake reads your vendors&apos; changelogs so you don&apos;t have to, ignores the changes that
            never touch your code, and opens the ones that do with the file, the line, the failing test and a
            proposed patch attached. Every finding is labelled confirmed, potential or unaffected, and the
            label is backed by evidence you can inspect.
          </p>
          <div className="mt-8 flex flex-wrap items-center gap-3">
            <SignInButton configured={authConfig.configured}>Sign in and connect a repo</SignInButton>
            <a
              href={REPO_URL}
              className="inline-flex items-center rounded-md border border-border px-4 py-2 text-sm font-medium text-fg hover:bg-surface-raised"
            >
              View the source
            </a>
          </div>
          {!authConfig.configured ? (
            <div role="status" className="mt-6 max-w-xl rounded-lg border border-border bg-surface p-4">
              <p className="text-sm font-medium text-fg">Sign-in is not available on this server</p>
              <p className="mt-1 text-sm text-fg-muted">
                Auth0 is not configured. Set the following environment variables and restart the frontend:
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
          ) : null}
        </section>

        {/* A real run */}
        <section aria-labelledby="real-run" className="border-t border-border py-14">
          <SectionHeading
            eyebrow="A real investigation"
            title="stripe-python 15.6.1 → 16.0.0"
            intro="From a run on this deployment against a small billing fixture. Real changelog, real model calls, real tests."
          />
          <h3 id="real-run" className="sr-only">
            Results of a real investigation
          </h3>
          <div className="mt-6 overflow-hidden rounded-lg border border-border bg-surface">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border px-4 py-3">
              <p className="text-sm text-fg">
                Tests <span className="font-medium">passed on 15.6.1</span> and{" "}
                <span className="font-medium">failed on 16.0.0</span>
              </p>
              <ImpactStatusBadge status="confirmed" size="sm" />
            </div>
            <ul className="divide-y divide-border">
              {RUN_ROWS.map((row) => (
                <li key={row.where} className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-center sm:justify-between">
                  <div className="min-w-0">
                    <p className="truncate font-mono text-xs text-fg">{row.where}</p>
                    <p className="mt-0.5 text-sm text-fg-muted">{row.what}</p>
                  </div>
                  <ImpactStatusBadge status={row.status} size="sm" />
                </li>
              ))}
            </ul>
            <div className="border-t border-border bg-surface-raised px-4 py-3">
              <p className="text-sm text-fg">
                <span className="font-medium">The sandbox caught a wrong patch.</span>{" "}
                <span className="text-fg-muted">
                  The model rewrote <code className="font-mono text-xs">invoice.request()</code> to{" "}
                  <code className="font-mono text-xs">raw_request()</code>, which returns a raw response. With all
                  patches applied: 3 tests passed, 1 failed, so the recommendation says &ldquo;Verified:
                  failed&rdquo; instead of claiming success.
                </span>
              </p>
            </div>
          </div>
        </section>

        {/* How it works */}
        <section aria-labelledby="how" className="border-t border-border py-14">
          <SectionHeading eyebrow="How it works" title="Deterministic code builds the evidence. The model explains it." />
          <h3 id="how" className="sr-only">
            How it works
          </h3>
          <ol className="mt-8 grid gap-4 sm:grid-cols-2">
            {STEPS.map((step, i) => (
              <li key={step.title} className="rounded-lg border border-border bg-surface p-5">
                <p className="font-mono text-xs text-fg-subtle">0{i + 1}</p>
                <p className="mt-1 text-base font-medium text-fg">{step.title}</p>
                <p className="mt-2 text-sm leading-relaxed text-fg-muted">{step.body}</p>
                <p className="mt-3 text-xs leading-relaxed text-fg-subtle">{step.detail}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Verdicts */}
        <section aria-labelledby="verdicts" className="border-t border-border py-14">
          <SectionHeading
            eyebrow="Three verdicts"
            title="A label is only as strong as its evidence"
            intro="The model can explain or downgrade a finding. It cannot upgrade one: that rule is enforced in code, not in a prompt."
          />
          <h3 id="verdicts" className="sr-only">
            Verdicts
          </h3>
          <dl className="mt-6 grid gap-4 sm:grid-cols-3">
            {VERDICTS.map((v) => (
              <div key={v.status} className="rounded-lg border border-border bg-surface p-5">
                <dt>
                  <ImpactStatusBadge status={v.status} />
                </dt>
                <dd className="mt-3 text-sm leading-relaxed text-fg-muted">{v.rule}</dd>
              </div>
            ))}
          </dl>
        </section>

        {/* Stack */}
        <section aria-labelledby="stack" className="border-t border-border py-14">
          <SectionHeading
            eyebrow="Built on open pieces"
            title="Inspectable end to end"
            intro="Apache-2.0 code, an open-weight model behind a plain OpenAI-compatible adapter, open-weight embeddings, and one adapter per service so the core logic never imports a vendor SDK."
          />
          <h3 id="stack" className="sr-only">
            Stack
          </h3>
          <ul className="mt-6 grid gap-x-8 gap-y-3 sm:grid-cols-2">
            {STACK.map((item) => (
              <li key={item.name} className="flex gap-3 text-sm">
                <span className="w-24 shrink-0 font-medium text-fg">{item.name}</span>
                <span className="text-fg-muted">{item.role}</span>
              </li>
            ))}
          </ul>
        </section>

        {/* Closing CTA */}
        <section className="border-t border-border py-14">
          <div className="flex flex-col gap-4 rounded-lg border border-border bg-surface p-6 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="text-base font-medium text-fg">Stop reading changelogs.</p>
              <p className="mt-1 text-sm text-fg-muted">
                Connect a repository, pick a vendor, and let the investigation tell you what actually breaks.
              </p>
            </div>
            <SignInButton configured={authConfig.configured}>Get started</SignInButton>
          </div>
          <p className="mt-4 text-xs text-fg-subtle">
            Free-tier demo: the first request after a quiet period can take about a minute while the server wakes
            up, and investigations take a few minutes on Google&apos;s free model tier.
          </p>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex w-full max-w-5xl flex-wrap items-center justify-between gap-2 px-4 py-4 text-xs text-fg-subtle sm:px-6">
          <p>Vendorwake is open source (Apache-2.0). Upstream-to-impact dependency intelligence.</p>
          <a href={REPO_URL} className="hover:text-fg">
            github.com/shaileshvats/vendorwake
          </a>
        </div>
      </footer>
    </div>
  );
}
