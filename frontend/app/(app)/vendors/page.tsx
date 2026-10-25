import type { Metadata } from "next";
import Link from "next/link";

import { ActionButton } from "@/components/action-button";
import { ApiErrorState } from "@/components/api-error";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { MonitoringStatusPill } from "@/components/status";
import { ExternalLink, linkClass, Mono, Pill, Time } from "@/components/ui";
import { discoverVendorAction, setMonitoringAction } from "@/lib/actions";
import { hostOf } from "@/lib/format";
import { listVendors } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Vendors" };

export default async function VendorsPage() {
  const vendors = await listVendors();

  return (
    <>
      <PageHeader
        title="Vendors"
        description="Upstream vendors Vendorwake can watch. Monitoring runs on a Temporal schedule; discovery runs once, now."
      />
      <Panel title="Vendors">
        {!vendors.ok ? (
          <ApiErrorState error={vendors.error} resource="the vendor list" />
        ) : vendors.data.length === 0 ? (
          <EmptyState title="No vendors configured." description="The API has no vendor signatures registered." />
        ) : (
          <ul className="flex flex-col gap-3">
            {vendors.data.map((vendor, index) => {
              const active = vendor.monitoring_status === "active";
              return (
                <li key={vendor.name}>
                  <article className="rounded-md border border-border" aria-labelledby={`vendor-${index}`}>
                    <header className="flex flex-wrap items-start justify-between gap-3 border-b border-border px-3 py-2.5">
                      <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2">
                          <h2 id={`vendor-${index}`} className="text-sm font-semibold text-fg">
                            {vendor.name}
                          </h2>
                          <Pill>{vendor.category}</Pill>
                          <MonitoringStatusPill status={vendor.monitoring_status} />
                        </div>
                        <p className="mt-1 text-xs text-fg-muted">
                          {vendor.website ? <ExternalLink href={vendor.website}>{hostOf(vendor.website)}</ExternalLink> : null}
                          {vendor.website ? " · " : null}
                          <Link href={`/changes?vendor=${encodeURIComponent(vendor.name)}`} className={linkClass}>
                            View changes
                          </Link>
                        </p>
                      </div>
                      <div className="flex flex-wrap items-start gap-2">
                        <ActionButton
                          action={setMonitoringAction.bind(null, vendor.name, !active)}
                          label={active ? "Stop monitoring" : "Start monitoring"}
                          pendingLabel={active ? "Stopping…" : "Starting…"}
                          variant={active ? "secondary" : "primary"}
                          successMessage={active ? "Monitoring paused." : "Monitoring scheduled."}
                        />
                        <ActionButton
                          action={discoverVendorAction.bind(null, vendor.name)}
                          label="Discover now"
                          pendingLabel="Starting discovery…"
                          successMessage="Discovery started; new changes appear on the Changes page."
                        />
                      </div>
                    </header>
                    <div className="grid grid-cols-1 gap-3 px-3 py-2.5 md:grid-cols-[1fr_2fr]">
                      <div>
                        <h3 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Official domains</h3>
                        <p className="mt-1 text-xs text-fg-muted">Only these count as authoritative sources.</p>
                        <ul className="mt-1.5 flex flex-wrap gap-1">
                          {vendor.official_domains.length === 0 ? <li className="text-xs text-fg-subtle">None listed</li> : null}
                          {vendor.official_domains.map((domain) => (
                            <li key={domain}>
                              <Mono className="rounded border border-border px-1 py-0.5">{domain}</Mono>
                            </li>
                          ))}
                        </ul>
                      </div>
                      <div>
                        <h3 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Monitored sources</h3>
                        {vendor.sources.length === 0 ? (
                          <p className="mt-1 text-xs text-fg-subtle">No sources registered.</p>
                        ) : (
                          <ul className="mt-1 divide-y divide-border">
                            {vendor.sources.map((source) => (
                              <li key={source.url} className="flex flex-wrap items-center justify-between gap-2 py-1.5 text-xs">
                                <span className="min-w-0 break-all">
                                  <Pill>{source.source_type}</Pill>{" "}
                                  <ExternalLink href={source.url}>{source.url}</ExternalLink>
                                </span>
                                <span className="text-fg-muted">
                                  last checked <Time value={source.last_checked_at} />
                                </span>
                              </li>
                            ))}
                          </ul>
                        )}
                      </div>
                    </div>
                  </article>
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
    </>
  );
}
