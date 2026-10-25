/**
 * TypeScript mirror of `backend/vendorwake/domain/contracts.py` (Pydantic models serialised as-is).
 * Optional Python fields with a `None` default are typed `T | null`; the API may also omit them.
 */

export type ChangeType =
  | "breaking"
  | "deprecation"
  | "removal"
  | "rename"
  | "schema_change"
  | "auth_change"
  | "rate_limit"
  | "security"
  | "sdk_version"
  | "behavioral"
  | "additive"
  | "other";

export type Severity = "critical" | "high" | "medium" | "low" | "info";

export type ImpactStatus = "confirmed" | "potential" | "unaffected" | "unknown";

export type VerificationStatus =
  | "pending"
  | "passed"
  | "failed"
  | "partially_verified"
  | "not_testable"
  | "requires_human_review";

export type EvidenceKind =
  /** Official announcement / changelog text. */
  | "vendor_source"
  /** Discovery hit; never proof on its own. */
  | "search_result"
  /** Manifest/lockfile entry. */
  | "dependency"
  /** Exact symbol usage found by static analysis. */
  | "code_reference"
  /** Installed version vs affected range. */
  | "version_check"
  /** Sandboxed verification output. */
  | "test_result"
  /** Model reasoning; never sufficient for CONFIRMED. */
  | "model_inference";

// ---------------------------------------------------------------------------- model calls

export interface ModelCallInfo {
  agent: string;
  provider: string;
  model: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  latency_s: number;
  /** True if the first output failed validation and a repair call fixed it. */
  repaired?: boolean;
}

// ---------------------------------------------------------------------------- vendors & sources

export interface DiscoveryCandidate {
  url: string;
  title: string;
  snippet?: string;
  query: string;
  rank: number;
  /** URL host is in the vendor's official_domains. */
  is_official: boolean;
}

// ---------------------------------------------------------------------------- extraction

export interface ExtractedChange {
  title: string;
  change_type: ChangeType;
  severity: Severity;
  affected_symbols: string[];
  replacement: string | null;
  /** Short verbatim quote from the source. */
  evidence_quote: string;
}

export interface ChangeExtraction {
  vendor: string;
  package: string;
  version: string;
  release_date: string | null;
  pinned_api_version: string | null;
  changes: ExtractedChange[];
  requires_human_review: boolean;
}

/** Deterministic check of a ChangeExtraction against its source text. */
export interface GroundingReport {
  ungrounded_quotes: string[];
  ungrounded_symbols: string[];
}

// ---------------------------------------------------------------------------- repository analysis

export interface RepoRef {
  provider: "github";
  owner: string;
  name: string;
  ref: string;
  subpath: string;
}

export interface DependencyFinding {
  ecosystem: string;
  package: string;
  version_spec: string | null;
  pinned_version: string | null;
  manifest_path: string;
  line?: number | null;
}

export interface ReferenceSite {
  file_path: string;
  line: number;
  symbol: string;
  snippet: string;
}

export interface IntegrationFinding {
  vendor: string;
  integration_type: "sdk" | "http_api" | "config";
  packages: DependencyFinding[];
  detected_version: string | null;
  references: ReferenceSite[];
  confidence: number;
}

export interface RepositoryInventory {
  repo: RepoRef;
  commit_sha: string | null;
  analyzed_at: string;
  files_scanned: number;
  dependencies: DependencyFinding[];
  integrations: IntegrationFinding[];
}

// ---------------------------------------------------------------------------- impact

export interface EvidenceItem {
  kind: EvidenceKind;
  deterministic: boolean;
  description: string;
  file_path?: string | null;
  line?: number | null;
  source_url?: string | null;
  content_sha256?: string | null;
  quote?: string | null;
}

export interface AffectedComponent {
  change_title: string;
  file_path: string;
  line: number | null;
  symbol: string;
  status: ImpactStatus;
  rationale: string;
  evidence: EvidenceItem[];
}

export type EvidenceNodeType = "vendor_source" | "change" | "symbol" | "integration" | "file" | "test" | "risk";

export interface EvidenceNode {
  id: string;
  type: EvidenceNodeType;
  label: string;
  status?: ImpactStatus | null;
}

export interface EvidenceEdge {
  source: string;
  target: string;
  label: string;
  /** Index into ImpactAssessment.evidence. */
  evidence_index?: number | null;
}

export interface EvidenceGraph {
  nodes: EvidenceNode[];
  edges: EvidenceEdge[];
}

export interface VerificationResult {
  status: VerificationStatus;
  command: string;
  exit_code: number | null;
  stdout_tail: string;
  stderr_tail: string;
  duration_s: number;
  /** e.g. "subprocess venv, python 3.12, stripe==16.0.0, network=off". */
  environment: string;
  before_version?: string | null;
  after_version?: string | null;
}

export interface Recommendation {
  recommendation_type: "code_change" | "dependency_pin" | "review" | "test";
  description: string;
  file_path?: string | null;
  /** Unified diff; generation is separate from application. */
  patch?: string | null;
  verification?: VerificationResult | null;
}

export interface ImpactAssessment {
  overall_status: ImpactStatus;
  severity: Severity;
  /** Model estimate, not a calibrated probability. */
  confidence: number;
  summary: string;
  affected: AffectedComponent[];
  evidence: EvidenceItem[];
  graph: EvidenceGraph;
  recommendations: Recommendation[];
  requires_human_review: boolean;
  model_calls: ModelCallInfo[];
}
