/** REST resource shapes from `docs/api-contract.md`. Times are ISO-8601 UTC strings; ids are UUID strings. */
import type {
  ChangeExtraction,
  ChangeType,
  DiscoveryCandidate,
  GroundingReport,
  ImpactAssessment,
  ImpactStatus,
  ModelCallInfo,
  Recommendation,
  Severity,
  VerificationStatus,
} from "@/types/contracts";

// ---------------------------------------------------------------------------- repositories

export type AnalysisStatus = "pending" | "running" | "completed" | "failed";

export interface Repository {
  id: string;
  provider: string;
  owner: string;
  name: string;
  ref: string;
  subpath: string;
  html_url: string;
  analysis_status: AnalysisStatus;
  last_analyzed_at: string | null;
  commit_sha: string | null;
  integration_count: number;
  created_at: string;
}

export interface ConnectRepositoryInput {
  owner: string;
  name: string;
  ref?: string;
  subpath?: string;
}

// ---------------------------------------------------------------------------- vendors & changes

export type MonitoringStatus = "active" | "paused" | "off";

export interface VendorSource {
  url: string;
  source_type: string;
  last_checked_at: string | null;
}

export interface Vendor {
  name: string;
  category: string;
  website: string | null;
  monitoring_status: MonitoringStatus;
  official_domains: string[];
  sources: VendorSource[];
}

export interface MonitoringStartResponse {
  vendor: string;
  monitoring_status: "active";
  schedule_id: string;
}

export interface MonitoringStopResponse {
  vendor: string;
  monitoring_status: "paused";
}

export interface WorkflowStarted {
  workflow_id: string;
}

export interface ChangeSummary {
  id: string;
  vendor: string;
  title: string;
  change_type: ChangeType;
  severity: Severity;
  published_at: string | null;
  detected_at: string;
  source_url: string;
  is_official: boolean;
  change_count: number;
}

export interface ChangeSource {
  url: string;
  retrieved_at: string;
  fetched_via: "http" | "browserbase";
  content_sha256: string;
  browser_session_id: string | null;
}

export interface AffectedRepositoryRef {
  repository_id: string;
  investigation_id: string;
  impact_status: ImpactStatus;
}

export interface ChangeDetail extends ChangeSummary {
  extraction: ChangeExtraction | null;
  grounding: (GroundingReport & { ok: boolean }) | null;
  source: ChangeSource | null;
  discovery: DiscoveryCandidate[];
  model_calls: ModelCallInfo[];
  affected_repositories: AffectedRepositoryRef[];
}

export interface RelatedChange {
  id: string;
  vendor: string;
  title: string;
  similarity: number;
  detected_at: string;
}

export interface InvestigationStarted {
  investigation_id: string;
  workflow_id: string;
}

// ---------------------------------------------------------------------------- investigations

export type InvestigationStatus = "queued" | "running" | "waiting_approval" | "completed" | "failed";

export interface InvestigationSummary {
  id: string;
  change: { id: string; vendor: string; title: string; severity: Severity };
  repository: { id: string; owner: string; name: string };
  status: InvestigationStatus;
  impact_status: ImpactStatus | null;
  confidence: number | null;
  created_at: string;
  completed_at: string | null;
}

export interface WorkflowRef {
  id: string;
  run_id: string | null;
  status: string;
  ui_url: string | null;
}

export interface TraceRef {
  sentry_trace_id: string;
  url: string | null;
}

export interface AgentSessionRef {
  provider: "entire";
  session_id: string;
  checkpoint_id: string | null;
  commit_sha: string | null;
  transcript_excerpt: string | null;
  url: string | null;
}

export interface TimelineEntry {
  at: string;
  step: string;
  status: string;
  detail: string | null;
}

export interface InvestigationDetail extends InvestigationSummary {
  summary: string | null;
  assessment: ImpactAssessment | null;
  workflow: WorkflowRef | null;
  trace: TraceRef | null;
  agent_session: AgentSessionRef | null;
  timeline: TimelineEntry[];
}

export type ApprovalStatus = "pending" | "approved" | "dismissed";

export interface RecommendationOut extends Recommendation {
  id: string;
  approval_status: ApprovalStatus;
  verification_status: VerificationStatus | null;
}

export interface RetrievedMemory {
  memory: string;
  score: number | null;
  id: string;
}

export interface AskResponse {
  answer: string;
  retrieved_memories: RetrievedMemory[];
  model: string;
}

// ---------------------------------------------------------------------------- security (Auth0 agents)

export type AgentOperation = "read_docs" | "read_repo" | "propose_patch" | "push_branch";

export interface AgentIdentity {
  agent: string;
  client_id: string;
  scopes: string[];
}

export interface PermissionCheckResult {
  agent: string;
  operation: AgentOperation;
  required_scope: string;
  allowed: boolean;
  reason: string;
}
