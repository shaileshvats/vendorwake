import type { Metadata } from "next";
import Link from "next/link";

import { ApiErrorState } from "@/components/api-error";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { linkClass, OfficialBadge, Pill, SeverityBadge, tableClass, tdClass, thClass, Time } from "@/components/ui";
import { humanize } from "@/lib/format";
import { listChanges, listVendors } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Changes" };

export default async function ChangesPage({
  searchParams,
}: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}) {
  const params = await searchParams;
  const vendor = typeof params.vendor === "string" && params.vendor ? params.vendor : undefined;
  const [changes, vendors] = await Promise.all([listChanges(vendor), listVendors()]);

  const filters = vendors.ok ? vendors.data.map((v) => v.name) : [];

  return (
    <>
      <PageHeader
        title="Changes"
        description="Upstream changes detected in monitored vendor changelogs and API docs, newest first."
      />
      {filters.length > 0 ? (
        <nav aria-label="Filter by vendor" className="mb-3 flex flex-wrap gap-1.5 text-sm">
          <Link
            href="/changes"
            aria-current={!vendor ? "page" : undefined}
            className={`rounded-md border px-2.5 py-1 ${!vendor ? "border-accent text-fg" : "border-border text-fg-muted hover:text-fg"}`}
          >
            All vendors
          </Link>
          {filters.map((name) => (
            <Link
              key={name}
              href={`/changes?vendor=${encodeURIComponent(name)}`}
              aria-current={vendor === name ? "page" : undefined}
              className={`rounded-md border px-2.5 py-1 ${vendor === name ? "border-accent text-fg" : "border-border text-fg-muted hover:text-fg"}`}
            >
              {name}
            </Link>
          ))}
        </nav>
      ) : null}

      <Panel title={vendor ? `Upstream changes · ${vendor}` : "Upstream changes"}>
        {!changes.ok ? (
          <ApiErrorState error={changes.error} resource="the change list" />
        ) : changes.data.length === 0 ? (
          <EmptyState
            title={vendor ? `No changes detected for ${vendor} yet.` : "No upstream changes detected yet."}
            description="Start monitoring a vendor or run discovery from the Vendors page."
          >
            <Link href="/vendors" className={`${linkClass} text-xs`}>
              Go to vendors
            </Link>
          </EmptyState>
        ) : (
          <div className="-mx-4 overflow-x-auto">
            <table className={tableClass}>
              <caption className="sr-only">Upstream changes</caption>
              <thead>
                <tr>
                  <th scope="col" className={thClass}>Change</th>
                  <th scope="col" className={thClass}>Vendor</th>
                  <th scope="col" className={thClass}>Type</th>
                  <th scope="col" className={thClass}>Severity</th>
                  <th scope="col" className={thClass}>Source</th>
                  <th scope="col" className={thClass}>Published</th>
                  <th scope="col" className={thClass}>Detected</th>
                </tr>
              </thead>
              <tbody>
                {[...changes.data]
                  .sort((a, b) => b.detected_at.localeCompare(a.detected_at))
                  .map((change) => (
                    <tr key={change.id}>
                      <th scope="row" className={`${tdClass} font-normal`}>
                        <Link href={`/changes/${change.id}`} className={linkClass}>
                          {change.title}
                        </Link>
                        {change.change_count > 0 ? (
                          <p className="text-xs text-fg-subtle">
                            {change.change_count} extracted change{change.change_count === 1 ? "" : "s"}
                          </p>
                        ) : null}
                      </th>
                      <td className={`${tdClass} text-fg-muted`}>{change.vendor}</td>
                      <td className={tdClass}>
                        <Pill>{humanize(change.change_type)}</Pill>
                      </td>
                      <td className={tdClass}>
                        <SeverityBadge severity={change.severity} />
                      </td>
                      <td className={tdClass}>
                        <OfficialBadge official={change.is_official} />
                      </td>
                      <td className={`${tdClass} text-xs text-fg-muted`}>
                        <Time value={change.published_at} />
                      </td>
                      <td className={`${tdClass} text-xs text-fg-muted`}>
                        <Time value={change.detected_at} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
