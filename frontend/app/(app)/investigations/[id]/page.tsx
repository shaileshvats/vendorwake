import type { Metadata } from "next";
import Link from "next/link";

import { ApiErrorState } from "@/components/api-error";
import { AskBox } from "@/components/ask-box";
import { AutoRefresh } from "@/components/auto-refresh";
import { EmptyState } from "@/components/empty-state";
import { EvidenceCard, FileRef, type RepoLinkInfo } from "@/components/evidence";
import { EvidenceGraph } from "@/components/evidence-graph";
import { ImpactStatusBadge } from "@/components/impact-status-badge";
import { ModelCallsTable } from "@/components/model-calls-table";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { RecommendationCard } from "@/components/recommendation-card";
import { InvestigationStatusPill, isInvestigationActive } from "@/components/status";
import {
  Callout,
  CodeBlock,
  ExternalLink,
  KeyValueList,
  linkClass,
  Mono,
  Pill,
  SeverityBadge,
  tableClass,
  tdClass,
  thClass,
  Time,
} from "@/components/ui";
import { formatPercent, hostOf, humanize, shortSha } from "@/lib/format";
import {
  getInvestigation,
  getInvestigationEvidence,
  getInvestigationRecommendations,
  getRepository,
} from "@/lib/vendorwake";
import type { EvidenceItem } from "@/types/contracts";

export const metadata: Metadata = { title: "Investigation" };

const TIMELINE_TONE: Record<string, string> = {
  completed: "bg-impact-unaffected",
  succeeded: "bg-impact-unaffected",
  ok: "bg-impact-unaffected",
  running: "bg-accent",
  started: "bg-accent",
  failed: "bg-impact-confirmed",
  error: "bg-impact-confirmed",
};

function EvidenceSection({
  title,
  description,
  items,
  repo,
}: {
  title: string;
  description: string;
  items: { item: EvidenceItem; index: number }[];
  repo: RepoLinkInfo | null;
}) {
  return (
    <section aria-label={title}>
      <h3 className="text-sm font-semibold text-fg">
        {title} <span className="font-normal text-fg-subtle">({items.length})</span>
      </h3>
      <p className="mt-0.5 text-xs text-fg-muted">{description}</p>
      {items.length === 0 ? (
        <p className="mt-2 text-xs text-fg-subtle">None.</p>
      ) : (
        <ol className="mt-2 flex flex-col gap-2">
          {items.map(({ item, index }) => (
            <li
              key={index}
              id={`evidence-${index}`}
              tabIndex={-1}
              className="scroll-mt-20 rounded-md border border-border p-3 target:border-accent"
            >
              <EvidenceCard item={item} index={index} repo={repo} />
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default async function InvestigationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const [investigation, recommendations] = await Promise.all([getInvestigation(id), getInvestigationRecommendations(id)]);

  if (!investigation.ok) {
    return (
      <>
        <PageHeader title="Investigation" description="Evidence-backed impact analysis." />
        <ApiErrorState error={investigation.error} resource="this investigation" />
        <Link href="/investigations" className={`${linkClass} mt-4 inline-block text-sm`}>
          Back to investigations
        </Link>
      </>
    );
  }

  const inv = investigation.data;
  const assessment = inv.assessment;
  const [repository, looseEvidence] = await Promise.all([
    getRepository(inv.repository.id),
    // Before an assessment exists, show whatever evidence has been collected so far.
    assessment ? Promise.resolve(null) : getInvestigationEvidence(id),
  ]);
  const repo: RepoLinkInfo | null = repository.ok ? repository.data : null;

  const evidence: EvidenceItem[] = assessment?.evidence ?? (looseEvidence?.ok ? looseEvidence.data : []);
  const indexed = evidence.map((item, index) => ({ item, index }));
  const deterministic = indexed.filter((e) => e.item.deterministic);
  const inferred = indexed.filter((e) => !e.item.deterministic);
  const sources = [
    ...new Map(
      evidence.filter((e) => e.source_url).map((e) => [e.source_url as string, e] as const),
    ).values(),
  ];

  const recs = recommendations.ok ? recommendations.data : (assessment?.recommendations ?? []);
  const verifying = recommendations.ok && recommendations.data.some((r) => r.verification_status === "pending");
  const active = isInvestigationActive(inv.status) || verifying;
  const overall = assessment?.overall_status ?? inv.impact_status;
  const confidence = assessment?.confidence ?? inv.confidence;
  const lastFailure = [...inv.timeline].reverse().find((t) => t.status === "failed" || t.status === "error");

  return (
    <>
      <PageHeader
        title={inv.change.title}
        description={`${inv.change.vendor} change investigated against ${inv.repository.owner}/${inv.repository.name}.`}
        actions={<AutoRefresh active={active} label={verifying ? "Verification running" : "Investigation running"} />}
      />

      <dl className="mb-4 grid grid-cols-2 gap-3 rounded-lg border border-border bg-surface p-4 sm:grid-cols-3 lg:grid-cols-6">
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Impact</dt>
          <dd className="mt-1">{overall ? <ImpactStatusBadge status={overall} /> : <span className="text-sm text-fg-muted">Pending</span>}</dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Status</dt>
          <dd className="mt-1">
            <InvestigationStatusPill status={inv.status} />
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Severity</dt>
          <dd className="mt-1">
            <SeverityBadge severity={assessment?.severity ?? inv.change.severity} />
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Confidence</dt>
          <dd className="mt-1 text-sm text-fg">
            <span className="tabular-nums">{formatPercent(confidence)}</span>{" "}
            <span className="text-xs text-fg-muted">model estimate</span>
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Started</dt>
          <dd className="mt-1 text-xs text-fg-muted">
            <Time value={inv.created_at} />
          </dd>
        </div>
        <div>
          <dt className="text-[11px] font-medium uppercase tracking-wide text-fg-subtle">Completed</dt>
          <dd className="mt-1 text-xs text-fg-muted">
            <Time value={inv.completed_at} />
          </dd>
        </div>
      </dl>

      <div className="mb-4 flex flex-col gap-2">
        {inv.status === "waiting_approval" ? (
          <Callout tone="warn" title="Waiting for your approval">
            The workflow is paused until you approve or dismiss a recommendation below.
          </Callout>
        ) : null}
        {inv.status === "failed" ? (
          <Callout tone="danger" title="The investigation failed">
            {lastFailure ? `${lastFailure.step}: ${lastFailure.detail ?? "no detail reported"}` : "See the timeline and workflow for details."}
          </Callout>
        ) : null}
        {assessment?.requires_human_review ? (
          <Callout tone="warn" title="Requires human review">
            The assessment flagged findings that automated checks could not settle.
          </Callout>
        ) : null}
        {!repository.ok ? <ApiErrorState error={repository.error} resource="the repository (line links are disabled)" /> : null}
      </div>

      <div className="mb-4 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="min-w-0 lg:col-span-2">
          <Panel title="Summary" className="h-full">
            {assessment?.summary || inv.summary ? (
              <p className="whitespace-pre-wrap text-sm leading-relaxed text-fg">{assessment?.summary || inv.summary}</p>
            ) : (
              <EmptyState
                title={isInvestigationActive(inv.status) ? "Assessment in progress…" : "No summary available."}
                description={isInvestigationActive(inv.status) ? "This page refreshes automatically while the workflow runs." : undefined}
              />
            )}
          </Panel>
        </div>
        <div className="min-w-0">
          <Panel title="Timeline" className="h-full">
            {inv.timeline.length === 0 ? (
              <EmptyState title="No steps recorded yet." />
            ) : (
              <ol className="relative ml-1 border-l border-border">
                {inv.timeline.map((step, i) => (
                  <li key={`${step.at}-${i}`} className="mb-3 ml-4 last:mb-0">
                    <span
                      aria-hidden="true"
                      className={`absolute -left-[5px] mt-1.5 size-2.5 rounded-full border border-canvas ${TIMELINE_TONE[step.status] ?? "bg-fg-subtle"}`}
                    />
                    <p className="flex flex-wrap items-center gap-1.5 text-sm text-fg">
                      {humanize(step.step)} <Pill>{step.status}</Pill>
                    </p>
                    <p className="text-[11px] text-fg-subtle">
                      <Time value={step.at} />
                    </p>
                    {step.detail ? <p className="mt-0.5 text-xs text-fg-muted">{step.detail}</p> : null}
                  </li>
                ))}
              </ol>
            )}
          </Panel>
        </div>
      </div>

      <div className="mb-4">
        <Panel
          title="Evidence graph"
          description="How the upstream source connects to your code. Select a node or connection to see the evidence item behind it."
        >
          {assessment ? (
            <EvidenceGraph graph={assessment.graph} evidence={assessment.evidence} repo={repo} />
          ) : (
            <EmptyState title="No graph yet." description="The graph is produced with the impact assessment." />
          )}
        </Panel>
      </div>

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="grid grid-cols-1 min-w-0 content-start gap-4 lg:col-span-2">


          <Panel title="Affected files and symbols" description="Each finding with its exact location and verdict.">
            {!assessment ? (
              <EmptyState title="No findings yet." />
            ) : assessment.affected.length === 0 ? (
              <EmptyState title="No affected code found." description="No reference to the changed symbols was found in this repository." />
            ) : (
              <div className="-mx-4 overflow-x-auto">
                <table className={tableClass}>
                  <caption className="sr-only">Affected files and symbols</caption>
                  <thead>
                    <tr>
                      <th scope="col" className={thClass}>Location</th>
                      <th scope="col" className={thClass}>Symbol</th>
                      <th scope="col" className={thClass}>Verdict</th>
                      <th scope="col" className={thClass}>Change and rationale</th>
                    </tr>
                  </thead>
                  <tbody>
                    {assessment.affected.map((a, i) => (
                      <tr key={`${a.file_path}:${a.line}:${i}`}>
                        <td className={tdClass}>
                          <FileRef repo={repo} path={a.file_path} line={a.line} />
                        </td>
                        <td className={tdClass}>
                          <Mono>{a.symbol}</Mono>
                        </td>
                        <td className={tdClass}>
                          <ImpactStatusBadge status={a.status} size="sm" />
                        </td>
                        <td className={`${tdClass} text-xs`}>
                          <p className="font-medium text-fg">{a.change_title}</p>
                          <p className="mt-0.5 text-fg-muted">{a.rationale}</p>
                          {a.evidence.length > 0 ? (
                            <p className="mt-1 text-[11px] text-fg-subtle">
                              {a.evidence.filter((e) => e.deterministic).length} deterministic ·{" "}
                              {a.evidence.filter((e) => !e.deterministic).length} inference
                            </p>
                          ) : null}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Panel>

          <Panel
            title="Evidence"
            description="Confirmed requires deterministic evidence; model inference alone can at most support Potential."
          >
            {looseEvidence && !looseEvidence.ok ? <ApiErrorState error={looseEvidence.error} resource="the evidence" /> : null}
            {evidence.length === 0 ? (
              looseEvidence && !looseEvidence.ok ? null : <EmptyState title="No evidence collected yet." />
            ) : (
              <div className="grid grid-cols-1 gap-5">
                <EvidenceSection
                  title="Deterministic evidence"
                  description="Static analysis, manifest entries, version checks, sandbox test results and hashed vendor sources."
                  items={deterministic}
                  repo={repo}
                />
                <EvidenceSection
                  title="Model inference and discovery"
                  description="Model reasoning and search results. Useful context, never proof on its own."
                  items={inferred}
                  repo={repo}
                />
              </div>
            )}
          </Panel>

          <Panel title="Recommendations" description="Proposed changes and their sandbox verification. Nothing is applied without your approval.">
            {!recommendations.ok ? (
              <div className="mb-3">
                <ApiErrorState error={recommendations.error} resource="the recommendations" />
              </div>
            ) : null}
            {recs.length === 0 ? (
              <EmptyState title="No recommendations." description={isInvestigationActive(inv.status) ? "Recommendations appear after the assessment." : undefined} />
            ) : (
              <div className="grid grid-cols-1 gap-4">
                {recs.map((rec, i) => (
                  <RecommendationCard key={"id" in rec ? String(rec.id) : i} recommendation={rec} index={i} repo={repo} />
                ))}
              </div>
            )}
          </Panel>

          <Panel
            title="Ask a follow-up"
            description="Answered from this investigation's stored context (Backboard). The memories each answer used are listed with it."
          >
            <AskBox investigationId={inv.id} />
          </Panel>

          {assessment ? (
            <Panel title="Model calls" description="Provider-reported usage for this assessment.">
              <ModelCallsTable calls={assessment.model_calls} />
            </Panel>
          ) : null}
        </div>

        <div className="grid grid-cols-1 min-w-0 content-start gap-4">

          <Panel title="Execution and provenance" description="Durable workflow, trace and agent session for this run.">
            <div className="flex flex-col gap-4">
              <section aria-label="Temporal workflow">
                <h3 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Temporal workflow</h3>
                {inv.workflow ? (
                  <KeyValueList
                    items={[
                      { label: "Workflow", value: <Mono>{inv.workflow.id}</Mono> },
                      { label: "Run", value: <Mono>{inv.workflow.run_id ?? "—"}</Mono> },
                      { label: "Status", value: <Pill>{inv.workflow.status}</Pill> },
                      {
                        label: "Open",
                        value: inv.workflow.ui_url ? <ExternalLink href={inv.workflow.ui_url}>Temporal UI</ExternalLink> : "—",
                      },
                    ]}
                  />
                ) : (
                  <p className="mt-1 text-xs text-fg-subtle">No workflow reported.</p>
                )}
              </section>
              <section aria-label="Sentry trace">
                <h3 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Sentry trace</h3>
                {inv.trace ? (
                  <KeyValueList
                    items={[
                      { label: "Trace", value: <Mono>{inv.trace.sentry_trace_id}</Mono> },
                      { label: "Open", value: inv.trace.url ? <ExternalLink href={inv.trace.url}>Sentry</ExternalLink> : "—" },
                    ]}
                  />
                ) : (
                  <p className="mt-1 text-xs text-fg-subtle">No trace recorded (Sentry may not be configured).</p>
                )}
              </section>
              <section aria-label="Entire agent session">
                <h3 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Agent session (Entire)</h3>
                {inv.agent_session ? (
                  <>
                    <KeyValueList
                      items={[
                        { label: "Session", value: <Mono>{inv.agent_session.session_id}</Mono> },
                        { label: "Checkpoint", value: <Mono>{inv.agent_session.checkpoint_id ?? "—"}</Mono> },
                        { label: "Commit", value: <Mono>{shortSha(inv.agent_session.commit_sha)}</Mono> },
                        ...(inv.agent_session.url
                          ? [{ label: "Open", value: <ExternalLink href={inv.agent_session.url}>Entire session</ExternalLink> }]
                          : []),
                      ]}
                    />
                    {inv.agent_session.transcript_excerpt ? (
                      <div className="mt-2">
                        <p className="mb-1 text-[11px] font-medium text-fg-subtle">Transcript excerpt</p>
                        <CodeBlock label="Agent transcript excerpt">{inv.agent_session.transcript_excerpt}</CodeBlock>
                      </div>
                    ) : null}
                  </>
                ) : (
                  <p className="mt-1 text-xs text-fg-subtle">No agent session recorded for this investigation.</p>
                )}
              </section>
            </div>
          </Panel>

          <Panel title="Research sources" description="Every URL cited by the evidence, with its content hash when fetched.">
            {sources.length === 0 ? (
              <EmptyState title="No sources cited yet." />
            ) : (
              <ul className="divide-y divide-border">
                {sources.map((s) => (
                  <li key={s.source_url} className="py-2 text-xs">
                    <ExternalLink href={s.source_url}>{hostOf(s.source_url as string)}</ExternalLink>
                    <p className="mt-0.5 break-all text-fg-subtle">{s.source_url}</p>
                    <p className="mt-1 flex flex-wrap items-center gap-1.5">
                      <Pill>{s.kind === "search_result" ? "Discovery, not proof" : humanize(s.kind)}</Pill>
                      {s.content_sha256 ? (
                        <span className="text-fg-subtle">
                          sha256 <Mono>{shortSha(s.content_sha256, 16)}</Mono>
                        </span>
                      ) : null}
                    </p>
                  </li>
                ))}
              </ul>
            )}
          </Panel>

          <Panel title="Related">
            <ul className="flex flex-col gap-1.5 text-sm">
              <li>
                <Link href={`/changes/${inv.change.id}`} className={linkClass}>
                  Upstream change
                </Link>
              </li>
              <li>
                <Link href={`/repositories/${inv.repository.id}`} className={linkClass}>
                  Repository {inv.repository.owner}/{inv.repository.name}
                </Link>
              </li>
            </ul>
          </Panel>
        </div>
      </div>
    </>
  );
}
