import { ActionButton } from "@/components/action-button";
import { DiffViewer } from "@/components/diff-viewer";
import { FileRef, type RepoLinkInfo } from "@/components/evidence";
import { ApprovalStatusPill, VerificationStatusPill } from "@/components/status";
import { CodeBlock, KeyValueList, Mono, Pill } from "@/components/ui";
import { recommendationAction } from "@/lib/actions";
import { formatSeconds, humanize } from "@/lib/format";
import type { Recommendation, VerificationResult } from "@/types/contracts";
import type { RecommendationOut } from "@/types/resources";

export function VerificationBlock({ result }: { result: VerificationResult }) {
  return (
    <div className="rounded-md border border-border p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h4 className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Sandbox verification</h4>
        <VerificationStatusPill status={result.status} />
      </div>
      <KeyValueList
        items={[
          {
            label: "Versions",
            value: (
              <span>
                <Mono>{result.before_version ?? "—"}</Mono> <span className="text-fg-muted">→</span>{" "}
                <Mono>{result.after_version ?? "—"}</Mono>
              </span>
            ),
          },
          { label: "Command", value: <Mono>{result.command}</Mono> },
          {
            label: "Exit code",
            value: (
              <span className="font-mono text-xs">
                {result.exit_code === null ? "— (did not exit normally)" : result.exit_code}
              </span>
            ),
          },
          { label: "Duration", value: formatSeconds(result.duration_s) },
          { label: "Environment", value: <Mono>{result.environment}</Mono> },
        ]}
      />
      {result.stdout_tail ? (
        <div className="mt-2">
          <p className="mb-1 text-[11px] font-medium text-fg-subtle">stdout (tail)</p>
          <CodeBlock label="Standard output tail">{result.stdout_tail}</CodeBlock>
        </div>
      ) : null}
      {result.stderr_tail ? (
        <div className="mt-2">
          <p className="mb-1 text-[11px] font-medium text-fg-subtle">stderr (tail)</p>
          <CodeBlock label="Standard error tail">{result.stderr_tail}</CodeBlock>
        </div>
      ) : null}
    </div>
  );
}

/** A recommendation, its proposed patch and verification, and the human decision controls. */
export function RecommendationCard({
  recommendation,
  index,
  repo,
}: {
  recommendation: RecommendationOut | Recommendation;
  index: number;
  repo: RepoLinkInfo | null;
}) {
  const managed = "id" in recommendation ? recommendation : null;
  const verifying = managed?.verification_status === "pending";
  const descId = `rec-${index}-desc`;
  return (
    <article className="rounded-md border border-border" aria-labelledby={`rec-${index}-title`}>
      <header className="flex flex-wrap items-center gap-2 border-b border-border px-3 py-2">
        <h3 id={`rec-${index}-title`} className="text-sm font-semibold text-fg">
          Recommendation {index + 1}
        </h3>
        <Pill tone="info">{humanize(recommendation.recommendation_type)}</Pill>
        {managed ? <ApprovalStatusPill status={managed.approval_status} /> : null}
        {managed?.verification_status ? <VerificationStatusPill status={managed.verification_status} /> : null}
      </header>
      <div className="flex flex-col gap-3 p-3">
        <p id={descId} className="text-sm text-fg">
          {recommendation.description}
        </p>
        {recommendation.file_path ? (
          <p className="text-xs text-fg-muted">
            File: <FileRef repo={repo} path={recommendation.file_path} />
          </p>
        ) : null}
        {recommendation.patch ? (
          <DiffViewer patch={recommendation.patch} label="Proposed patch (not applied)" />
        ) : (
          <p className="text-xs text-fg-subtle">No patch proposed.</p>
        )}
        {recommendation.verification ? (
          <VerificationBlock result={recommendation.verification} />
        ) : (
          <p className="text-xs text-fg-subtle">Not verified in the sandbox yet.</p>
        )}
        {managed ? (
          <div className="flex flex-wrap items-start gap-2 border-t border-border pt-3">
            <ActionButton
              action={recommendationAction.bind(null, managed.id, "approve")}
              label={managed.approval_status === "approved" ? "Approved" : "Approve"}
              pendingLabel="Approving…"
              variant="primary"
              disabled={managed.approval_status === "approved"}
              describedBy={descId}
            />
            <ActionButton
              action={recommendationAction.bind(null, managed.id, "dismiss")}
              label={managed.approval_status === "dismissed" ? "Dismissed" : "Dismiss"}
              pendingLabel="Dismissing…"
              disabled={managed.approval_status === "dismissed"}
              describedBy={descId}
            />
            <ActionButton
              action={recommendationAction.bind(null, managed.id, "verify")}
              label={verifying ? "Verification running" : "Re-verify"}
              pendingLabel="Queuing verification…"
              successMessage="Verification queued."
              disabled={verifying}
              describedBy={descId}
            />
            <p className="basis-full text-[11px] text-fg-subtle">
              Approve or dismiss records your decision on this recommendation. Nothing is pushed: pushing a branch needs the repo:write scope, which no agent holds.
            </p>
          </div>
        ) : (
          <p className="border-t border-border pt-3 text-xs text-fg-subtle">
            Decision controls are unavailable because the recommendations endpoint did not respond.
          </p>
        )}
      </div>
    </article>
  );
}
