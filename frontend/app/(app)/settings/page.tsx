import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { getCurrentUser } from "@/lib/session";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const user = await getCurrentUser();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account, organization, integrations, and notification preferences."
      />
      <div className="grid gap-4 md:grid-cols-2">
        <Panel title="Account" description="From your Auth0 session.">
          {user ? (
            <dl className="divide-y divide-border text-sm">
              <div className="flex justify-between gap-4 py-2">
                <dt className="text-fg-muted">Name</dt>
                <dd className="truncate text-fg">{user.name ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-4 py-2">
                <dt className="text-fg-muted">Email</dt>
                <dd className="truncate text-fg">{user.email ?? "—"}</dd>
              </div>
              <div className="flex justify-between gap-4 py-2">
                <dt className="text-fg-muted">Subject</dt>
                <dd className="truncate font-mono text-xs text-fg">{user.sub}</dd>
              </div>
            </dl>
          ) : null}
        </Panel>
        <Panel title="Integrations" description="GitHub and other connected services.">
          <EmptyState title="No integrations configured." description="Integration settings are not available yet." />
        </Panel>
        <Panel title="Notifications" description="Where Vendorwake sends alerts about affected integrations.">
          <EmptyState title="No notification channels configured." description="Notification settings are not available yet." />
        </Panel>
      </div>
    </>
  );
}
