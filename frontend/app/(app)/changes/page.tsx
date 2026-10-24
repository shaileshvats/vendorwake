import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";

export const metadata: Metadata = { title: "Changes" };

export default function ChangesPage() {
  return (
    <>
      <PageHeader
        title="Changes"
        description="Upstream changes detected in monitored vendor changelogs and API docs, newest first."
      />
      <Panel title="Upstream changes">
        <EmptyState
          title="No upstream changes detected yet."
          description="Changes will appear here once vendors are being monitored."
        />
      </Panel>
    </>
  );
}
