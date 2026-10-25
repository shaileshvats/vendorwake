import type { Metadata } from "next";
import Link from "next/link";

import { ApiErrorState } from "@/components/api-error";
import { AutoRefresh } from "@/components/auto-refresh";
import { ConnectRepositoryForm } from "@/components/connect-repository-form";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { AnalysisStatusPill, isAnalysisActive } from "@/components/status";
import { linkClass, Mono, tableClass, tdClass, thClass, Time } from "@/components/ui";
import { shortSha } from "@/lib/format";
import { listRepositories } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Repositories" };

export default async function RepositoriesPage() {
  const repos = await listRepositories();
  const anyActive = repos.ok && repos.data.some((repo) => isAnalysisActive(repo.analysis_status));

  return (
    <>
      <PageHeader
        title="Repositories"
        description="GitHub repositories you connect, with the vendor SDKs and API calls Vendorwake finds in each."
        actions={<AutoRefresh active={anyActive} label="Analysis running" />}
      />
      <div className="grid grid-cols-1 gap-4">
        <Panel
          title="Connect a repository"
          description="Public GitHub repositories are read with a read-only token. Analysis starts immediately."
        >
          <ConnectRepositoryForm />
        </Panel>

        <Panel title="Connected repositories">
          {!repos.ok ? (
            <ApiErrorState error={repos.error} resource="the repository list" />
          ) : repos.data.length === 0 ? (
            <EmptyState
              title="No repositories connected yet."
              description="Connect one above, or use the demo fixture to see the stripe-python 16.0.0 scenario."
            />
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className={tableClass}>
                <caption className="sr-only">Connected repositories</caption>
                <thead>
                  <tr>
                    <th scope="col" className={thClass}>Repository</th>
                    <th scope="col" className={thClass}>Ref</th>
                    <th scope="col" className={thClass}>Analysis</th>
                    <th scope="col" className={`${thClass} text-right`}>Integrations</th>
                    <th scope="col" className={thClass}>Commit</th>
                    <th scope="col" className={thClass}>Last analyzed</th>
                  </tr>
                </thead>
                <tbody>
                  {repos.data.map((repo) => (
                    <tr key={repo.id}>
                      <th scope="row" className={`${tdClass} font-normal`}>
                        <Link href={`/repositories/${repo.id}`} className={linkClass}>
                          {repo.owner}/{repo.name}
                        </Link>
                        {repo.subpath ? <p className="font-mono text-xs text-fg-subtle">/{repo.subpath}</p> : null}
                      </th>
                      <td className={tdClass}>
                        <Mono>{repo.ref}</Mono>
                      </td>
                      <td className={tdClass}>
                        <AnalysisStatusPill status={repo.analysis_status} />
                      </td>
                      <td className={`${tdClass} text-right tabular-nums`}>{repo.integration_count}</td>
                      <td className={tdClass}>
                        <Mono>{shortSha(repo.commit_sha, 10)}</Mono>
                      </td>
                      <td className={`${tdClass} text-xs text-fg-muted`}>
                        <Time value={repo.last_analyzed_at} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
