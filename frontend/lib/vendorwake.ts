import "server-only";

import { apiRequest } from "@/lib/api";
import type { EvidenceItem, RepositoryInventory } from "@/types/contracts";
import type { ApiResult } from "@/types/api";
import type {
  AgentIdentity,
  AgentOperation,
  AskResponse,
  ChangeDetail,
  ChangeSummary,
  ConnectRepositoryInput,
  InvestigationDetail,
  InvestigationStarted,
  InvestigationSummary,
  MonitoringStartResponse,
  MonitoringStopResponse,
  PermissionCheckResult,
  RecommendationOut,
  RelatedChange,
  Repository,
  Vendor,
  WorkflowStarted,
} from "@/types/resources";

/*
 * Typed, authenticated wrappers for every endpoint in docs/api-contract.md. Validation is
 * deliberately shallow (top-level shape and identifiers): enough to turn a contract mismatch
 * into an "unexpected response" state instead of a render crash.
 */

type Validator = (data: unknown) => boolean;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function hasStrings(...keys: string[]): Validator {
  return (value) => isRecord(value) && keys.every((key) => typeof value[key] === "string");
}

function arrayOf(item: Validator): Validator {
  return (value) => Array.isArray(value) && value.every(item);
}

const isRepository = hasStrings("id", "owner", "name", "analysis_status");
const isVendor = (value: unknown) => hasStrings("name", "monitoring_status")(value) && Array.isArray((value as Record<string, unknown>).sources);
const isChangeSummary = hasStrings("id", "vendor", "title", "severity", "detected_at");
const isChangeDetail = (value: unknown) =>
  isChangeSummary(value) &&
  Array.isArray((value as Record<string, unknown>).discovery) &&
  Array.isArray((value as Record<string, unknown>).model_calls);
const isRelatedChange = hasStrings("id", "title");
const isInvestigationSummary = (value: unknown) =>
  hasStrings("id", "status", "created_at")(value) &&
  isRecord((value as Record<string, unknown>).change) &&
  isRecord((value as Record<string, unknown>).repository);
const isInvestigationDetail = (value: unknown) =>
  isInvestigationSummary(value) && Array.isArray((value as Record<string, unknown>).timeline);
const isEvidenceItem = hasStrings("kind", "description");
const isRecommendationOut = hasStrings("id", "recommendation_type", "description", "approval_status");
const isInventory = (value: unknown) =>
  isRecord(value) && Array.isArray(value.dependencies) && Array.isArray(value.integrations);
const isAgentIdentity = (value: unknown) =>
  hasStrings("agent", "client_id")(value) && Array.isArray((value as Record<string, unknown>).scopes);
const isPermissionCheck = (value: unknown) =>
  hasStrings("agent", "operation", "required_scope")(value) && typeof (value as Record<string, unknown>).allowed === "boolean";
const isAskResponse = (value: unknown) =>
  hasStrings("answer")(value) && Array.isArray((value as Record<string, unknown>).retrieved_memories);

const id = (value: string) => encodeURIComponent(value);

function get<T>(path: string, validate: Validator): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { auth: true, validate });
}

function post<T>(path: string, body: unknown, validate: Validator, timeoutMs = 15_000): Promise<ApiResult<T>> {
  return apiRequest<T>(path, { auth: true, method: "POST", body, validate, timeoutMs });
}

// ---------------------------------------------------------------------------- repositories

export const listRepositories = () => get<Repository[]>("/api/repositories", arrayOf(isRepository));
export const getRepository = (repositoryId: string) =>
  get<Repository>(`/api/repositories/${id(repositoryId)}`, isRepository);
export const getRepositoryInventory = (repositoryId: string) =>
  get<RepositoryInventory>(`/api/repositories/${id(repositoryId)}/dependencies`, isInventory);
export const connectRepository = (input: ConnectRepositoryInput) =>
  post<Repository>("/api/repositories", input, isRepository);
export const analyzeRepository = (repositoryId: string) =>
  post<Repository>(`/api/repositories/${id(repositoryId)}/analyze`, undefined, isRepository);

// ---------------------------------------------------------------------------- vendors & changes

export const listVendors = () => get<Vendor[]>("/api/vendors", arrayOf(isVendor));
export const startMonitoring = (vendor: string) =>
  post<MonitoringStartResponse>("/api/monitoring/start", { vendor }, hasStrings("vendor", "monitoring_status"));
export const stopMonitoring = (vendor: string) =>
  post<MonitoringStopResponse>("/api/monitoring/stop", { vendor }, hasStrings("vendor", "monitoring_status"));
export const discoverVendor = (vendor: string) =>
  post<WorkflowStarted>(`/api/vendors/${id(vendor)}/discover`, undefined, hasStrings("workflow_id"));

export const listChanges = (vendor?: string) =>
  get<ChangeSummary[]>(
    vendor ? `/api/changes?${new URLSearchParams({ vendor }).toString()}` : "/api/changes",
    arrayOf(isChangeSummary),
  );
export const getChange = (changeId: string) => get<ChangeDetail>(`/api/changes/${id(changeId)}`, isChangeDetail);
export const getRelatedChanges = (changeId: string) =>
  get<RelatedChange[]>(`/api/changes/${id(changeId)}/related`, arrayOf(isRelatedChange));
export const investigateChange = (changeId: string, repositoryId: string) =>
  post<InvestigationStarted>(
    `/api/changes/${id(changeId)}/investigate`,
    { repository_id: repositoryId },
    hasStrings("investigation_id"),
  );

// ---------------------------------------------------------------------------- investigations

export const listInvestigations = () =>
  get<InvestigationSummary[]>("/api/investigations", arrayOf(isInvestigationSummary));
export const getInvestigation = (investigationId: string) =>
  get<InvestigationDetail>(`/api/investigations/${id(investigationId)}`, isInvestigationDetail);
export const getInvestigationEvidence = (investigationId: string) =>
  get<EvidenceItem[]>(`/api/investigations/${id(investigationId)}/evidence`, arrayOf(isEvidenceItem));
export const getInvestigationRecommendations = (investigationId: string) =>
  get<RecommendationOut[]>(
    `/api/investigations/${id(investigationId)}/recommendations`,
    arrayOf(isRecommendationOut),
  );
export const askInvestigation = (investigationId: string, question: string) =>
  // Follow-ups run a model call through Backboard, so allow more time than reads.
  post<AskResponse>(`/api/investigations/${id(investigationId)}/ask`, { question }, isAskResponse, 60_000);

export type RecommendationAction = "approve" | "dismiss" | "verify";
export const actOnRecommendation = (recommendationId: string, action: RecommendationAction) =>
  post<RecommendationOut>(`/api/recommendations/${id(recommendationId)}/${action}`, undefined, isRecommendationOut);

// ---------------------------------------------------------------------------- security

export const listAgents = () => get<AgentIdentity[]>("/api/security/agents", arrayOf(isAgentIdentity));
export const checkAgentPermission = (agent: string, operation: AgentOperation) =>
  post<PermissionCheckResult>("/api/security/agents/check", { agent, operation }, isPermissionCheck, 20_000);
