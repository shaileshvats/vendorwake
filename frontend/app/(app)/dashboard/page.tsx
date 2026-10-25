import type { Metadata } from "next";
import Link from "next/link";
import { Suspense } from "react";

import { ApiErrorState } from "@/components/api-error";
import { EmptyState } from "@/components/empty-state";
import { ImpactStatusBadge } from "@/components/impact-status-badge";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { InvestigationStatusPill, isInvestigationActive } from "@/components/status";
import { SystemStatus, SystemStatusFallback } from "@/components/system-status";
import { linkClass, OfficialBadge, SeverityBadge, StatTile, Time } from "@/components/ui";
import { listChanges, listInvestigations, listRepositories, listVendors } from "@/lib/vendorwake";
import type { ApiResult } from "@/types/api";
import type { InvestigationSummary } from "@/types/resources";

export const metadata: Metadata = { title: "Dashboard" };

const NEW_CHANGE_WINDOW_DAYS = 7;
const HIGH_RISK = new Set(["critical", "high"]);

/** Pages render per request, so "new" is relative to the request time. */
function newChangeCutoff(): number {
  return Date.now() - NEW_CHANGE_WINDOW_DAYS * 86_400_000;
}

function count<T>(result: ApiResult<T[]>, predicate: (item: T) => boolean = () => true): string {
  return result.ok ? String(result.data.filter(predicate).length) : "—";
}

function InvestigationRows({ items, empty }: { items: InvestigationSummary[]; empty: string }) {
  if (items.length === 0) return <EmptyState title={empty} />;
  return (
    <ul className="divide-y divide-border">
      {items.map((inv) => (
        <li key={inv.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
          <div className="min-w-0">
            <Link href={`/investigations/${inv.id}`} className={`${linkClass} text-sm`}>
              {inv.change.title}
            </Link>
            <p className="text-xs text-fg-muted">
              {inv.change.vendor} → {inv.repository.owner}/{inv.repository.name}
            </p>
          </div>
          <div className="flex items-center gap-1.5">
            <SeverityBadge severity={inv.change.severity} />
            {inv.impact_status ? <ImpactStatusBadge status={inv.impact_status} size="sm" /> : <InvestigationStatusPill status={inv.status} />}
          </div>
        </li>
      ))}
    </ul>
  );
}

export default async function DashboardPage() {
  const [repos, vendors, changes, investigations] = await Promise.all([
    listRepositories(),
    listVendors(),
    listChanges(),
    listInvestigations(),
  ]);

  const cutoff = newChangeCutoff();
  const isNew = (detectedAt: string) => new Date(detectedAt).getTime() >= cutoff;

  const invs = investigations.ok ? investigations.data : [];
  const highRisk = invs.filter(
    (inv) => (inv.impact_status === "confirmed" || inv.impact_status === "potential") && HIGH_RISK.has(inv.change.severity),
  );
  const pending = invs.filter((inv) => isInvestigationActive(inv.status) || inv.status === "waiting_approval");
  const confirmed = invs.filter((inv) => inv.impact_status === "confirmed");
  const completed = invs
    .filter((inv) => inv.status === "completed")
    .sort((a, b) => (b.completed_at ?? "").localeCompare(a.completed_at ?? ""))
    .slice(0, 5);
  const newChanges = changes.ok
    ? changes.data.filter((c) => isNew(c.detected_at)).sort((a, b) => b.detected_at.localeCompare(a.detected_at)).slice(0, 6)
    : [];

  const firstError = [repos, vendors, changes, investigations].find((r) => !r.ok);

  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Upstream changes, the code they touch, and the investigations that settle their impact."
      />

      {firstError && !firstError.ok ? (
        <div className="mb-4">
          <ApiErrorState error={firstError.error} />
        </div>
      ) : null}

      <div className="mb-4 grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Repositories" value={count(repos)} hint="connected" href="/repositories" />
        <StatTile
          label="Vendors"
          value={count(vendors, (v) => v.monitoring_status === "active")}
          hint="monitored"
          href="/vendors"
        />
        <StatTile
          label="New changes"
          value={count(changes, (c) => isNew(c.detected_at))}
          hint={`detected in ${NEW_CHANGE_WINDOW_DAYS} days`}
          href="/changes"
        />
        <StatTile label="High-risk" value={investigations.ok ? String(highRisk.length) : "—"} hint="confirmed/potential, high+" href="/investigations" />
        <StatTile label="Pending" value={investigations.ok ? String(pending.length) : "—"} hint="investigations" href="/investigations" />
        <StatTile label="Confirmed" value={investigations.ok ? String(confirmed.length) : "—"} hint="affected integrations" href="/investigations" />
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="grid grid-cols-1 content-start gap-4 lg:col-span-2">
          <Panel title="New upstream changes" description={`Detected in the last ${NEW_CHANGE_WINDOW_DAYS} days.`}>
            {!changes.ok ? (
              <ApiErrorState error={changes.error} />
            ) : newChanges.length === 0 ? (
              <EmptyState
                title="No new upstream changes."
                description="Start monitoring a vendor or run discovery to detect changes."
              >
                <Link href="/vendors" className={`${linkClass} text-xs`}>
                  Go to vendors
                </Link>
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border">
                {newChanges.map((change) => (
                  <li key={change.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                    <div className="min-w-0">
                      <Link href={`/changes/${change.id}`} className={`${linkClass} text-sm`}>
                        {change.title}
                      </Link>
                      <p className="text-xs text-fg-muted">
                        {change.vendor} · <Time value={change.detected_at} />
                      </p>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <SeverityBadge severity={change.severity} />
                      <OfficialBadge official={change.is_official} />
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Panel title="High-risk findings" description="Confirmed or potential impact from high or critical changes.">
              {investigations.ok ? (
                <InvestigationRows items={highRisk.slice(0, 5)} empty="No high-risk findings." />
              ) : (
                <ApiErrorState error={investigations.error} />
              )}
            </Panel>
            <Panel title="Pending investigations" description="Queued, running, or awaiting your approval.">
              {investigations.ok ? (
                <InvestigationRows items={pending.slice(0, 5)} empty="No investigations pending." />
              ) : (
                <ApiErrorState error={investigations.error} />
              )}
            </Panel>
            <Panel title="Confirmed affected integrations" description="Impact backed by deterministic evidence.">
              {investigations.ok ? (
                <InvestigationRows items={confirmed.slice(0, 5)} empty="No confirmed affected integrations." />
              ) : (
                <ApiErrorState error={investigations.error} />
              )}
            </Panel>
            <Panel title="Recently completed" description="Investigations that finished with a verdict.">
              {!investigations.ok ? (
                <ApiErrorState error={investigations.error} />
              ) : completed.length === 0 ? (
                <EmptyState title="No completed investigations yet." />
              ) : (
                <ul className="divide-y divide-border">
                  {completed.map((inv) => (
                    <li key={inv.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
                      <div className="min-w-0">
                        <Link href={`/investigations/${inv.id}`} className={`${linkClass} text-sm`}>
                          {inv.change.title}
                        </Link>
                        <p className="text-xs text-fg-muted">
                          {inv.repository.owner}/{inv.repository.name} · <Time value={inv.completed_at} />
                        </p>
                      </div>
                      {inv.impact_status ? <ImpactStatusBadge status={inv.impact_status} size="sm" /> : null}
                    </li>
                  ))}
                </ul>
              )}
            </Panel>
          </div>
        </div>

        <div className="grid grid-cols-1 content-start gap-4">
          <Panel title="Repositories" description="Connected and analysed.">
            {!repos.ok ? (
              <ApiErrorState error={repos.error} />
            ) : repos.data.length === 0 ? (
              <EmptyState title="No repositories connected yet.">
                <Link href="/repositories" className={`${linkClass} text-xs`}>
                  Connect a repository
                </Link>
              </EmptyState>
            ) : (
              <ul className="divide-y divide-border">
                {repos.data.slice(0, 6).map((repo) => (
                  <li key={repo.id} className="flex items-center justify-between gap-2 py-2 text-sm">
                    <Link href={`/repositories/${repo.id}`} className={`${linkClass} min-w-0 truncate`}>
                      {repo.owner}/{repo.name}
                      {repo.subpath ? <span className="text-fg-subtle">/{repo.subpath}</span> : null}
                    </Link>
                    <span className="shrink-0 text-xs tabular-nums text-fg-muted">{repo.integration_count} integrations</span>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
          <Panel title="System status" description="Live checks against the Vendorwake API.">
            <Suspense fallback={<SystemStatusFallback />}>
              <SystemStatus />
            </Suspense>
          </Panel>
        </div>
      </div>
    </>
  );
}
