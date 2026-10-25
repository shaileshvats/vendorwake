"use server";

/*
 * Server Actions for every mutation. They run on the Next.js server, where lib/api.ts attaches the
 * signed-in user's Auth0 access token; the browser only ever sees the action result.
 */

import { AGENT_OPERATIONS } from "@/lib/agent-operations";
import * as api from "@/lib/vendorwake";
import type { ApiError, ApiResult } from "@/types/api";
import type {
  AgentOperation,
  AskResponse,
  InvestigationStarted,
  MonitoringStartResponse,
  MonitoringStopResponse,
  PermissionCheckResult,
  RecommendationOut,
  Repository,
  WorkflowStarted,
} from "@/types/resources";

export type ActionError = Pick<ApiError, "kind" | "message" | "status" | "code">;
export type ActionResult<T> = { ok: true; data: T } | { ok: false; error: ActionError };

function toAction<T>(result: ApiResult<T>): ActionResult<T> {
  if (result.ok) return { ok: true, data: result.data };
  const { kind, message, status, code } = result.error;
  return { ok: false, error: { kind, message, status, code } };
}

function invalid(message: string): ActionResult<never> {
  return { ok: false, error: { kind: "validation_error", message } };
}

// GitHub owner/repo names and git refs; subpath is a relative directory.
const NAME_RE = /^[A-Za-z0-9._-]{1,100}$/;
const REF_RE = /^[A-Za-z0-9._/-]{1,200}$/;
const SUBPATH_RE = /^[A-Za-z0-9._/-]{0,300}$/;
const ID_RE = /^[A-Za-z0-9-]{1,64}$/;
const VENDOR_RE = /^[A-Za-z0-9._ -]{1,100}$/;

export async function connectRepositoryAction(input: {
  owner: string;
  name: string;
  ref: string;
  subpath: string;
}): Promise<ActionResult<Repository>> {
  const owner = input.owner.trim();
  const name = input.name.trim().replace(/\.git$/, "");
  const ref = input.ref.trim() || "main";
  const subpath = input.subpath.trim().replace(/^\/+|\/+$/g, "");
  if (!NAME_RE.test(owner)) return invalid("Owner must be a GitHub user or organization name.");
  if (!NAME_RE.test(name)) return invalid("Name must be a GitHub repository name.");
  if (!REF_RE.test(ref)) return invalid("Ref must be a branch, tag or commit.");
  if (!SUBPATH_RE.test(subpath) || subpath.split("/").includes("..")) {
    return invalid("Subpath must be a relative directory inside the repository.");
  }
  return toAction(await api.connectRepository({ owner, name, ref, subpath }));
}

export async function analyzeRepositoryAction(repositoryId: string): Promise<ActionResult<Repository>> {
  if (!ID_RE.test(repositoryId)) return invalid("Invalid repository id.");
  return toAction(await api.analyzeRepository(repositoryId));
}

export async function setMonitoringAction(
  vendor: string,
  enabled: boolean,
): Promise<ActionResult<MonitoringStartResponse | MonitoringStopResponse>> {
  if (!VENDOR_RE.test(vendor)) return invalid("Invalid vendor name.");
  if (enabled) return toAction<MonitoringStartResponse | MonitoringStopResponse>(await api.startMonitoring(vendor));
  return toAction<MonitoringStartResponse | MonitoringStopResponse>(await api.stopMonitoring(vendor));
}

export async function discoverVendorAction(vendor: string): Promise<ActionResult<WorkflowStarted>> {
  if (!VENDOR_RE.test(vendor)) return invalid("Invalid vendor name.");
  return toAction(await api.discoverVendor(vendor));
}

export async function investigateChangeAction(
  changeId: string,
  repositoryId: string,
): Promise<ActionResult<InvestigationStarted>> {
  if (!ID_RE.test(changeId) || !ID_RE.test(repositoryId)) return invalid("Choose a repository to investigate against.");
  return toAction(await api.investigateChange(changeId, repositoryId));
}

export async function askInvestigationAction(
  investigationId: string,
  question: string,
): Promise<ActionResult<AskResponse>> {
  const trimmed = question.trim();
  if (!ID_RE.test(investigationId)) return invalid("Invalid investigation id.");
  if (!trimmed) return invalid("Enter a question.");
  if (trimmed.length > 2000) return invalid("Questions are limited to 2,000 characters.");
  return toAction(await api.askInvestigation(investigationId, trimmed));
}

export async function recommendationAction(
  recommendationId: string,
  action: api.RecommendationAction,
): Promise<ActionResult<RecommendationOut>> {
  if (!ID_RE.test(recommendationId)) return invalid("Invalid recommendation id.");
  if (action !== "approve" && action !== "dismiss" && action !== "verify") return invalid("Unknown action.");
  return toAction(await api.actOnRecommendation(recommendationId, action));
}

export async function checkAgentPermissionAction(
  agent: string,
  operation: AgentOperation,
): Promise<ActionResult<PermissionCheckResult>> {
  if (!NAME_RE.test(agent)) return invalid("Choose an agent.");
  if (!AGENT_OPERATIONS.some((op) => op.value === operation)) return invalid("Choose an operation.");
  return toAction(await api.checkAgentPermission(agent, operation));
}
