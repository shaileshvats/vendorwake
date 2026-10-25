import type { Metadata } from "next";
import Link from "next/link";

import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { SettingsTabs } from "@/components/settings-tabs";
import { linkClass } from "@/components/ui";
import { getCurrentUser } from "@/lib/session";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage() {
  const user = await getCurrentUser();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account, agent identities, and the permissions each agent holds."
      />
      <SettingsTabs current="/settings" />
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
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
        <Panel title="Agent security" description="Auth0 machine identities for Vendorwake's agents.">
          <p className="text-sm text-fg-muted">
            See which scopes each agent holds and test what it is allowed to do on the{" "}
            <Link href="/settings/security" className={linkClass}>
              Security
            </Link>{" "}
            tab.
          </p>
        </Panel>
      </div>
    </>
  );
}
