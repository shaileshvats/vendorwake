import type { AgentOperation } from "@/types/resources";

/** Operations the API's permission check understands, with the Auth0 API scope each requires. */
export const AGENT_OPERATIONS: { value: AgentOperation; label: string; scope: string }[] = [
  { value: "read_docs", label: "Read vendor docs", scope: "docs:read" },
  { value: "read_repo", label: "Read repository", scope: "repo:read" },
  { value: "propose_patch", label: "Propose patch", scope: "patch:propose" },
  { value: "push_branch", label: "Push branch", scope: "repo:write" },
];
