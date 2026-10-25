import type { Metadata } from "next";
import Link from "next/link";

import { ApiErrorState } from "@/components/api-error";
import { EmptyState } from "@/components/empty-state";
import { ImpactStatusBadge } from "@/components/impact-status-badge";
import { InvestigateForm } from "@/components/investigate-form";
import { ModelCallsTable } from "@/components/model-calls-table";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import {
  Callout,
  ExternalLink,
  KeyValueList,
  linkClass,
  Mono,
  OfficialBadge,
  Pill,
  SeverityBadge,
  Time,
} from "@/components/ui";
import { formatDate, formatPercent, hostOf, humanize } from "@/lib/format";
import { getChange, getRelatedChanges, listRepositories } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Change" };

export default async function ChangePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [change, related, repos] = await Promise.all([getChange(id), getRelatedChanges(id), listRepositories()]);

  if (!change.ok) {
    return (
      <>
        <PageHeader title="Change" description="Upstream change detail." />
        <ApiErrorState error={change.error} resource="this change" />
        <Link href="/changes" className={`${linkClass} mt-4 inline-block text-sm`}>
          Back to changes
        </Link>
      </>
    );
  }

  const c = change.data;
  const extraction = c.extraction;
  const grounding = c.grounding;
  const ungroundedQuotes = new Set(grounding?.ungrounded_quotes ?? []);
  const ungroundedSymbols = new Set(grounding?.ungrounded_symbols ?? []);
  const repoById = new Map((repos.ok ? repos.data : []).map((r) => [r.id, r]));

  return (
    <>
      <PageHeader
        title={c.title}
        description={`${c.vendor} · ${humanize(c.change_type)} · detected ${formatDate(c.detected_at)}`}
      />
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <SeverityBadge severity={c.severity} />
        <Pill>{humanize(c.change_type)}</Pill>
        <OfficialBadge official={c.is_official} />
        {extraction?.requires_human_review ? <Pill tone="warn">Extraction needs human review</Pill> : null}
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="grid grid-cols-1 content-start gap-4 lg:col-span-2">
          <Panel title="Source" description="Where this change was read from, and proof of exactly what was read.">
            {c.source ? (
              <KeyValueList
                items={[
                  { label: "URL", value: <ExternalLink href={c.source.url}>{c.source.url}</ExternalLink> },
                  { label: "Retrieved", value: <Time value={c.source.retrieved_at} /> },
                  { label: "Content SHA-256", value: <Mono>{c.source.content_sha256}</Mono> },
                  {
                    label: "Fetched via",
                    value: (
                      <span className="flex flex-wrap items-center gap-2">
                        <Pill tone={c.source.fetched_via === "browserbase" ? "info" : "neutral"}>
                          {c.source.fetched_via === "browserbase" ? "Browserbase" : "HTTP"}
                        </Pill>
                        {c.source.browser_session_id ? (
                          <span className="text-xs text-fg-muted">
                            session <Mono>{c.source.browser_session_id}</Mono>
                          </span>
                        ) : null}
                      </span>
                    ),
                  },
                  { label: "Published", value: <Time value={c.published_at} /> },
                ]}
              />
            ) : (
              <KeyValueList
                items={[
                  { label: "URL", value: <ExternalLink href={c.source_url}>{c.source_url}</ExternalLink> },
                  { label: "Retrieval", value: <span className="text-fg-muted">No retrieval record reported.</span> },
                ]}
              />
            )}
          </Panel>

          <Panel
            title="Extracted changes"
            description="Model extraction from the source. Every quote is checked verbatim against the retrieved text."
          >
            {!extraction ? (
              <EmptyState title="No extraction available." description="The source has not been processed by the extraction agent yet." />
            ) : (
              <>
                <div className="mb-3">
                  {grounding ? (
                    grounding.ok ? (
                      <Callout tone="ok" title="Grounding passed">
                        All {extraction.changes.length} evidence quotes and every affected symbol appear verbatim in the retrieved source.
                      </Callout>
                    ) : (
                      <Callout tone="danger" title="Grounding failed: some model output is not in the source">
                        {grounding.ungrounded_quotes.length} quote(s) and {grounding.ungrounded_symbols.length} symbol(s) were not found
                        verbatim. Treat the marked items as unverified.
                        {grounding.ungrounded_symbols.length > 0 ? (
                          <span className="mt-1 block">
                            Ungrounded symbols:{" "}
                            {grounding.ungrounded_symbols.map((s) => (
                              <Mono key={s} className="mr-1.5">
                                {s}
                              </Mono>
                            ))}
                          </span>
                        ) : null}
                      </Callout>
                    )
                  ) : (
                    <Callout tone="neutral" title="No grounding report">
                      The deterministic grounding check has not run for this extraction.
                    </Callout>
                  )}
                </div>
                <KeyValueList
                  items={[
                    { label: "Package", value: <Mono>{`${extraction.package} ${extraction.version}`}</Mono> },
                    { label: "Release date", value: extraction.release_date ?? "—" },
                    ...(extraction.pinned_api_version
                      ? [{ label: "Pinned API version", value: <Mono>{extraction.pinned_api_version}</Mono> }]
                      : []),
                  ]}
                />
                <ol className="mt-3 flex flex-col gap-3">
                  {extraction.changes.map((item, index) => {
                    const quoteGrounded = !ungroundedQuotes.has(item.evidence_quote);
                    return (
                      <li key={index} className="rounded-md border border-border p-3">
                        <div className="flex flex-wrap items-center gap-2">
                          <h3 className="text-sm font-medium text-fg">{item.title}</h3>
                          <Pill>{humanize(item.change_type)}</Pill>
                          <SeverityBadge severity={item.severity} />
                        </div>
                        {item.affected_symbols.length > 0 ? (
                          <p className="mt-2 text-xs text-fg-muted">
                            Affected:{" "}
                            {item.affected_symbols.map((symbol) => (
                              <span key={symbol} className="mr-2">
                                <Mono>{symbol}</Mono>
                                {ungroundedSymbols.has(symbol) ? <span className="ml-1 text-danger">(not in source)</span> : null}
                              </span>
                            ))}
                          </p>
                        ) : null}
                        {item.replacement ? (
                          <p className="mt-1 text-xs text-fg-muted">
                            Replacement: <Mono>{item.replacement}</Mono>
                          </p>
                        ) : null}
                        <figure className="mt-2">
                          <blockquote
                            className={`border-l-2 pl-3 font-mono text-xs leading-relaxed text-fg ${
                              quoteGrounded ? "border-impact-unaffected" : "border-impact-confirmed"
                            }`}
                          >
                            {item.evidence_quote}
                          </blockquote>
                          <figcaption className="mt-1 text-[11px] text-fg-subtle">
                            {grounding
                              ? quoteGrounded
                                ? "Verbatim quote, found in the retrieved source."
                                : "Quote NOT found in the retrieved source."
                              : "Verbatim quote (grounding not checked)."}
                          </figcaption>
                        </figure>
                      </li>
                    );
                  })}
                </ol>
              </>
            )}
          </Panel>

          <Panel
            title="Discovery results"
            description="Search hits (SerpApi) used to find candidate sources. Discovery, not proof: only fetched official sources count as evidence."
          >
            {c.discovery.length === 0 ? (
              <EmptyState title="No discovery results recorded." />
            ) : (
              <ol className="divide-y divide-border">
                {[...c.discovery]
                  .sort((a, b) => a.rank - b.rank)
                  .map((hit) => (
                    <li key={`${hit.query}-${hit.rank}-${hit.url}`} className="py-2">
                      <div className="flex flex-wrap items-center gap-2 text-sm">
                        <span className="font-mono text-xs text-fg-subtle">#{hit.rank}</span>
                        <ExternalLink href={hit.url}>{hit.title || hostOf(hit.url)}</ExternalLink>
                        <Pill tone="neutral">Discovery, not proof</Pill>
                        <OfficialBadge official={hit.is_official} />
                      </div>
                      {hit.snippet ? <p className="mt-1 text-xs text-fg-muted">{hit.snippet}</p> : null}
                      <p className="mt-0.5 text-[11px] text-fg-subtle">
                        {hostOf(hit.url)} · query <Mono>{hit.query}</Mono>
                      </p>
                    </li>
                  ))}
              </ol>
            )}
          </Panel>

          <Panel title="Model calls" description="Provider-reported usage for extracting this change.">
            <ModelCallsTable calls={c.model_calls} />
          </Panel>
        </div>

        <div className="grid grid-cols-1 content-start gap-4">
          <Panel title="Investigate against a repository" description="Maps this change to code and produces an evidence-backed verdict.">
            {!repos.ok ? (
              <ApiErrorState error={repos.error} />
            ) : repos.data.length === 0 ? (
              <EmptyState title="No repositories connected.">
                <Link href="/repositories" className={`${linkClass} text-xs`}>
                  Connect a repository
                </Link>
              </EmptyState>
            ) : (
              <InvestigateForm
                changeId={c.id}
                repositories={repos.data.map((r) => ({
                  id: r.id,
                  label: `${r.owner}/${r.name}${r.subpath ? `/${r.subpath}` : ""}`,
                  ready: r.analysis_status === "completed",
                }))}
              />
            )}
          </Panel>

          <Panel title="Investigations of this change">
            {c.affected_repositories.length === 0 ? (
              <EmptyState title="Not investigated yet." />
            ) : (
              <ul className="divide-y divide-border">
                {c.affected_repositories.map((entry) => {
                  const repo = repoById.get(entry.repository_id);
                  return (
                    <li key={entry.investigation_id} className="flex items-center justify-between gap-2 py-2 text-sm">
                      <Link href={`/investigations/${entry.investigation_id}`} className={`${linkClass} min-w-0 truncate`}>
                        {repo ? `${repo.owner}/${repo.name}` : entry.repository_id}
                      </Link>
                      <ImpactStatusBadge status={entry.impact_status} size="sm" />
                    </li>
                  );
                })}
              </ul>
            )}
          </Panel>

          <Panel title="Related historical changes" description="Similar past changes by embedding similarity.">
            {!related.ok ? (
              <ApiErrorState error={related.error} />
            ) : related.data.length === 0 ? (
              <EmptyState title="No similar past changes." />
            ) : (
              <ul className="divide-y divide-border">
                {related.data.map((item) => (
                  <li key={item.id} className="py-2">
                    <Link href={`/changes/${item.id}`} className={`${linkClass} text-sm`}>
                      {item.title}
                    </Link>
                    <p className="text-xs text-fg-muted">
                      {item.vendor} · similarity {formatPercent(item.similarity)} · <Time value={item.detected_at} />
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>
        </div>
      </div>
    </>
  );
}
