import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { IMPACT_STATUSES, ImpactStatusBadge, impactStatusDescription } from "@/components/impact-status-badge";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";

export const metadata: Metadata = { title: "Investigations" };

export default function InvestigationsPage() {
  return (
    <>
      <PageHeader
        title="Investigations"
        description="Evidence-backed analyses of how an upstream change affects each integration in your code."
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Panel title="All investigations" className="lg:col-span-2">
          <EmptyState
            title="No investigations yet."
            description="Investigations will be listed here with their status and the impact verdict for each affected integration."
          />
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
        </Panel>
      </div>
    </>
  );
}
