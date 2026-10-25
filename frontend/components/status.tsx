import { Pill, type Tone } from "@/components/ui";
import type { VerificationStatus } from "@/types/contracts";
import type { AnalysisStatus, ApprovalStatus, InvestigationStatus, MonitoringStatus } from "@/types/resources";

/* Text-labelled status pills for lifecycle states (impact verdicts use ImpactStatusBadge). */

function make<T extends string>(map: Record<T, [string, Tone]>, prefix: string) {
  return function StatusPill({ status }: { status: T | null | undefined }) {
    if (!status) return <span className="text-fg-subtle">—</span>;
    const [label, tone] = map[status] ?? [String(status).replace(/_/g, " "), "neutral"];
    return (
      <Pill tone={tone}>
        <span className="sr-only">{prefix}: </span>
        {label}
      </Pill>
    );
  };
}

export const AnalysisStatusPill = make<AnalysisStatus>(
  {
    pending: ["Pending", "neutral"],
    running: ["Analyzing", "info"],
    completed: ["Analyzed", "ok"],
    failed: ["Analysis failed", "danger"],
  },
  "Analysis",
);

export const InvestigationStatusPill = make<InvestigationStatus>(
  {
    queued: ["Queued", "neutral"],
    running: ["Running", "info"],
    waiting_approval: ["Awaiting approval", "warn"],
    completed: ["Completed", "ok"],
    failed: ["Failed", "danger"],
  },
  "Investigation",
);

export const VerificationStatusPill = make<VerificationStatus>(
  {
    pending: ["Verification pending", "info"],
    passed: ["Verified: passed", "ok"],
    failed: ["Verified: failed", "danger"],
    partially_verified: ["Partially verified", "warn"],
    not_testable: ["Not testable", "neutral"],
    requires_human_review: ["Needs human review", "warn"],
  },
  "Verification",
);

export const ApprovalStatusPill = make<ApprovalStatus>(
  {
    pending: ["Awaiting decision", "warn"],
    approved: ["Approved", "ok"],
    dismissed: ["Dismissed", "neutral"],
  },
  "Approval",
);

export const MonitoringStatusPill = make<MonitoringStatus>(
  {
    active: ["Monitoring", "ok"],
    paused: ["Paused", "warn"],
    off: ["Off", "neutral"],
  },
  "Monitoring",
);

export const isInvestigationActive = (status: InvestigationStatus) => status === "queued" || status === "running";
export const isAnalysisActive = (status: AnalysisStatus) => status === "pending" || status === "running";
