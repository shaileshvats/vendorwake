import type { Metadata } from "next";
import { Suspense } from "react";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { SystemStatus, SystemStatusFallback } from "@/components/system-status";

export const metadata: Metadata = { title: "Dashboard" };

/*
 * Dashboard sections (PRD §11.1). None of these have backing API endpoints in M1, so each
 * renders an honest empty state rather than placeholder data.
 */
const SECTIONS = [
  {
    title: "Connected repositories",
    description: "GitHub repositories Vendorwake maps upstream changes against.",
    empty: "No repositories connected yet.",
    hint: "Repository connection is not available yet.",
  },
  {
    title: "Monitored vendors",
    description: "Upstream changelogs and API docs being watched.",
    empty: "No vendors monitored yet.",
    hint: "Vendor monitoring is not available yet.",
  },
  {
    title: "New upstream changes",
    description: "Recently detected changes from monitored vendors.",
    empty: "No upstream changes detected yet.",
    hint: "Changes appear once at least one vendor is monitored.",
  },
  {
    title: "High-risk findings",
    description: "Changes with confirmed or likely impact on your code.",
    empty: "No high-risk findings.",
    hint: "Findings are produced by investigations of upstream changes.",
  },
  {
    title: "Pending investigations",
    description: "Investigations queued or in progress.",
    empty: "No investigations pending.",
  },
  {
    title: "Confirmed affected integrations",
    description: "Integrations with evidence-backed confirmed impact.",
    empty: "No confirmed affected integrations.",
  },
  {
    title: "Recently completed investigations",
    description: "Investigations that finished with a verdict.",
    empty: "No completed investigations yet.",
  },
] as const;

export default function DashboardPage() {
  return (
    <>
      <PageHeader
        title="Dashboard"
        description="Upstream changes, the code they touch, and the investigations that settle their impact."
      />

      <div className="grid gap-4 lg:grid-cols-3">
        <div className="grid gap-4 md:grid-cols-2 lg:col-span-2">
          {SECTIONS.map((section) => (
            <Panel key={section.title} title={section.title} description={section.description}>
              <EmptyState title={section.empty} description={"hint" in section ? section.hint : undefined} />
            </Panel>
          ))}
        </div>

        <div className="lg:col-span-1">
          <Panel title="System status" description="Live checks against the Vendorwake API." className="lg:sticky lg:top-20">
            <Suspense fallback={<SystemStatusFallback />}>
              <SystemStatus />
            </Suspense>
          </Panel>
        </div>
      </div>
    </>
  );
}
