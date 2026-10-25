import type { Metadata } from "next";
import Link from "next/link";

import { ActionButton } from "@/components/action-button";
import { ApiErrorState } from "@/components/api-error";
import { AutoRefresh } from "@/components/auto-refresh";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { AnalysisStatusPill, isAnalysisActive } from "@/components/status";
import {
  Callout,
  ExternalLink,
  KeyValueList,
  linkClass,
  Mono,
  Pill,
  tableClass,
  tdClass,
  thClass,
  Time,
} from "@/components/ui";
import { analyzeRepositoryAction } from "@/lib/actions";
import { formatNumber, formatPercent, githubFileUrl, shortSha } from "@/lib/format";
import { getRepository, getRepositoryInventory } from "@/lib/vendorwake";
import type { Repository } from "@/types/resources";

export const metadata: Metadata = { title: "Repository" };

function FileLink({ repo, path, line }: { repo: Repository; path: string; line?: number | null }) {
  const href = githubFileUrl(repo, path, line);
  const text = `${path}${line ? `:${line}` : ""}`;
  return href ? (
    <ExternalLink href={href} className="font-mono text-xs">
      {text}
    </ExternalLink>
  ) : (
    <Mono>{text}</Mono>
  );
}

export default async function RepositoryPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const repo = await getRepository(id);

  if (!repo.ok) {
    return (
      <>
        <PageHeader title="Repository" description="Connected repository and its detected vendor integrations." />
        <ApiErrorState error={repo.error} resource="this repository" />
        <Link href="/repositories" className={`${linkClass} mt-4 inline-block text-sm`}>
          Back to repositories
        </Link>
      </>
    );
  }

  const r = repo.data;
  const active = isAnalysisActive(r.analysis_status);
  const inventory = await getRepositoryInventory(id);

  return (
    <>
      <PageHeader
        title={`${r.owner}/${r.name}`}
        description={r.subpath ? `Analyzing subpath /${r.subpath} at ${r.ref}.` : `Analyzing ${r.ref}.`}
        actions={
          <div className="flex items-start gap-3">
            <AutoRefresh active={active} label="Analysis running" />
            <ActionButton
              action={analyzeRepositoryAction.bind(null, r.id)}
              label="Re-analyze"
              pendingLabel="Starting…"
              successMessage="Analysis started."
              disabled={active}
            />
          </div>
        }
      />

      <div className="grid grid-cols-1 gap-4">
        <Panel title="Analysis">
          <KeyValueList
            items={[
              { label: "Status", value: <AnalysisStatusPill status={r.analysis_status} /> },
              { label: "GitHub", value: <ExternalLink href={r.html_url}>{r.html_url}</ExternalLink> },
              { label: "Ref", value: <Mono>{r.ref}</Mono> },
              { label: "Subpath", value: r.subpath ? <Mono>/{r.subpath}</Mono> : "Repository root" },
              { label: "Commit analyzed", value: <Mono>{shortSha(r.commit_sha, 40)}</Mono> },
              { label: "Last analyzed", value: <Time value={r.last_analyzed_at} /> },
              { label: "Connected", value: <Time value={r.created_at} /> },
              ...(inventory.ok ? [{ label: "Files scanned", value: formatNumber(inventory.data.files_scanned) }] : []),
            ]}
          />
          {r.analysis_status === "failed" ? (
            <div className="mt-3">
              <Callout tone="danger" title="The last analysis failed.">
                Re-analyze to try again. The API does not report the failure reason on this endpoint.
              </Callout>
            </div>
          ) : null}
        </Panel>

        {!inventory.ok ? (
          inventory.error.kind === "not_found" ? (
            <Panel title="Detected integrations">
              <EmptyState
                title={active ? "Analysis in progress…" : "No analysis results yet."}
                description={
                  active
                    ? "Dependencies and integrations appear here when the first analysis finishes. This page refreshes automatically."
                    : "Run an analysis to detect vendor SDKs, API hosts and configuration in this repository."
                }
              />
            </Panel>
          ) : (
            <ApiErrorState error={inventory.error} resource="the analysis results" />
          )
        ) : (
          <>
            <Panel
              title={`Detected integrations (${inventory.data.integrations.length})`}
              description="Vendor usage found by static analysis: imports, API hosts and environment variables, with exact file and line."
            >
              {inventory.data.integrations.length === 0 ? (
                <EmptyState title="No vendor integrations detected." description="None of the known vendor signatures matched this code." />
              ) : (
                <div className="grid grid-cols-1 gap-4">
                  {inventory.data.integrations.map((integration, index) => (
                    <article key={`${integration.vendor}-${index}`} className="rounded-md border border-border">
                      <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
                        <h3 className="text-sm font-semibold text-fg">{integration.vendor}</h3>
                        <Pill>{integration.integration_type.replace("_", " ")}</Pill>
                        <span className="text-xs text-fg-muted">
                          version <Mono>{integration.detected_version ?? "unknown"}</Mono>
                        </span>
                        <span className="ml-auto text-xs text-fg-muted" title="Static-analysis match confidence">
                          detection confidence {formatPercent(integration.confidence)}
                        </span>
                      </header>
                      {integration.packages.length > 0 ? (
                        <p className="border-b border-border px-3 py-2 text-xs text-fg-muted">
                          Packages:{" "}
                          {integration.packages.map((pkg, i) => (
                            <span key={`${pkg.package}-${i}`} className="mr-3">
                              <Mono>
                                {pkg.package}
                                {pkg.version_spec ?? ""}
                              </Mono>{" "}
                              in <FileLink repo={r} path={pkg.manifest_path} line={pkg.line} />
                            </span>
                          ))}
                        </p>
                      ) : null}
                      {integration.references.length === 0 ? (
                        <p className="px-3 py-2 text-xs text-fg-muted">No code references recorded.</p>
                      ) : (
                        <div className="overflow-x-auto">
                          <table className={tableClass}>
                            <caption className="sr-only">{integration.vendor} code references</caption>
                            <thead>
                              <tr>
                                <th scope="col" className={thClass}>Location</th>
                                <th scope="col" className={thClass}>Symbol</th>
                                <th scope="col" className={thClass}>Source line</th>
                              </tr>
                            </thead>
                            <tbody>
                              {integration.references.map((ref, i) => (
                                <tr key={`${ref.file_path}:${ref.line}:${i}`}>
                                  <td className={tdClass}>
                                    <FileLink repo={r} path={ref.file_path} line={ref.line} />
                                  </td>
                                  <td className={tdClass}>
                                    <Mono>{ref.symbol}</Mono>
                                  </td>
                                  <td className={tdClass}>
                                    <code className="whitespace-pre-wrap break-all font-mono text-xs text-fg-muted">{ref.snippet}</code>
                                  </td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </article>
                  ))}
                </div>
              )}
            </Panel>

            <Panel title={`Dependencies (${inventory.data.dependencies.length})`} description="Manifest and lockfile entries.">
              {inventory.data.dependencies.length === 0 ? (
                <EmptyState title="No dependency manifests found." />
              ) : (
                <div className="-mx-4 overflow-x-auto">
                  <table className={tableClass}>
                    <caption className="sr-only">Dependencies</caption>
                    <thead>
                      <tr>
                        <th scope="col" className={thClass}>Package</th>
                        <th scope="col" className={thClass}>Ecosystem</th>
                        <th scope="col" className={thClass}>Spec</th>
                        <th scope="col" className={thClass}>Pinned</th>
                        <th scope="col" className={thClass}>Declared in</th>
                      </tr>
                    </thead>
                    <tbody>
                      {inventory.data.dependencies.map((dep, i) => (
                        <tr key={`${dep.ecosystem}:${dep.package}:${i}`}>
                          <th scope="row" className={`${tdClass} font-normal`}>
                            <Mono>{dep.package}</Mono>
                          </th>
                          <td className={`${tdClass} text-xs text-fg-muted`}>{dep.ecosystem}</td>
                          <td className={tdClass}>
                            <Mono>{dep.version_spec ?? "—"}</Mono>
                          </td>
                          <td className={tdClass}>
                            <Mono>{dep.pinned_version ?? "—"}</Mono>
                          </td>
                          <td className={tdClass}>
                            <FileLink repo={r} path={dep.manifest_path} line={dep.line} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Panel>
          </>
        )}
      </div>
    </>
  );
}
