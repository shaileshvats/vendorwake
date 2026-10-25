import type { Metadata } from "next";
import Link from "next/link";

import { ApiErrorState } from "@/components/api-error";
import { AutoRefresh } from "@/components/auto-refresh";
import { EmptyState } from "@/components/empty-state";
import { IMPACT_STATUSES, ImpactStatusBadge, impactStatusDescription } from "@/components/impact-status-badge";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { InvestigationStatusPill, isInvestigationActive } from "@/components/status";
import { linkClass, SeverityBadge, tableClass, tdClass, thClass, Time } from "@/components/ui";
import { formatPercent } from "@/lib/format";
import { listInvestigations } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Investigations" };

export default async function InvestigationsPage() {
  const investigations = await listInvestigations();
  const anyActive = investigations.ok && investigations.data.some((inv) => isInvestigationActive(inv.status));

  return (
    <>
      <PageHeader
        title="Investigations"
        description="Evidence-backed analyses of how an upstream change affects each integration in your code."
        actions={<AutoRefresh active={anyActive} label="Investigations running" />}
      />
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
        <Panel title="All investigations" className="xl:col-span-3">
          {!investigations.ok ? (
            <ApiErrorState error={investigations.error} resource="the investigation list" />
          ) : investigations.data.length === 0 ? (
            <EmptyState
              title="No investigations yet."
              description="Open an upstream change and choose “Investigate against a repository”."
            >
              <Link href="/changes" className={`${linkClass} text-xs`}>
                Go to changes
              </Link>
            </EmptyState>
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className={tableClass}>
                <caption className="sr-only">Investigations</caption>
                <thead>
                  <tr>
                    <th scope="col" className={thClass}>Change</th>
                    <th scope="col" className={thClass}>Repository</th>
                    <th scope="col" className={thClass}>Status</th>
                    <th scope="col" className={thClass}>Impact</th>
                    <th scope="col" className={`${thClass} text-right`}>Confidence</th>
                    <th scope="col" className={thClass}>Started</th>
                  </tr>
                </thead>
                <tbody>
                  {[...investigations.data]
                    .sort((a, b) => b.created_at.localeCompare(a.created_at))
                    .map((inv) => (
                      <tr key={inv.id}>
                        <th scope="row" className={`${tdClass} font-normal`}>
                          <Link href={`/investigations/${inv.id}`} className={linkClass}>
                            {inv.change.title}
                          </Link>
                          <p className="mt-0.5 flex items-center gap-1.5 text-xs text-fg-muted">
                            {inv.change.vendor} <SeverityBadge severity={inv.change.severity} />
                          </p>
                        </th>
                        <td className={`${tdClass} text-fg-muted`}>
                          {inv.repository.owner}/{inv.repository.name}
                        </td>
                        <td className={tdClass}>
                          <InvestigationStatusPill status={inv.status} />
                        </td>
                        <td className={tdClass}>
                          {inv.impact_status ? <ImpactStatusBadge status={inv.impact_status} size="sm" /> : <span className="text-fg-subtle">—</span>}
                        </td>
                        <td className={`${tdClass} text-right tabular-nums text-fg-muted`} title="Model estimate, not a calibrated probability">
                          {formatPercent(inv.confidence)}
                        </td>
                        <td className={`${tdClass} text-xs text-fg-muted`}>
                          <Time value={inv.created_at} />
                        </td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
        <Panel title="Impact statuses" description="How each integration is classified.">
          <dl className="flex flex-col gap-3">
            {IMPACT_STATUSES.map((status) => (
              <div key={status}>
                <dt>
                  <ImpactStatusBadge status={status} />
                </dt>
                <dd className="mt-1 text-xs text-fg-muted">{impactStatusDescription(status)}</dd>
              </div>
            ))}
          </dl>
          <p className="mt-3 border-t border-border pt-3 text-xs text-fg-muted">
            Confirmed requires deterministic evidence (an exact code reference plus an affected version, or a test result).
            Model output alone can at most produce Potential.
          </p>
        </Panel>
      </div>
    </>
  );
}
