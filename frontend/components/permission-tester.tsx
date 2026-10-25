"use client";

import { useId, useState, useTransition } from "react";

import { InlineError } from "@/components/api-error";
import { buttonClass, inputClass, Mono, Pill } from "@/components/ui";
import { checkAgentPermissionAction, type ActionError } from "@/lib/actions";
import { AGENT_OPERATIONS } from "@/lib/agent-operations";
import type { AgentOperation, PermissionCheckResult } from "@/types/resources";


interface Check {
  at: string;
  result: PermissionCheckResult;
}

/** Runs the API's real token request + scope check for an agent identity and shows the decision. */
export function PermissionTester({ agents }: { agents: string[] }) {
  const agentId = useId();
  const opId = useId();
  const [agent, setAgent] = useState(agents[0] ?? "");
  const [operation, setOperation] = useState<AgentOperation>("push_branch");
  const [history, setHistory] = useState<Check[]>([]);
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <div className="flex flex-col gap-3">
      <form
        aria-busy={pending}
        className="flex flex-wrap items-end gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setError(null);
          startTransition(async () => {
            const result = await checkAgentPermissionAction(agent, operation);
            if (!result.ok) {
              setError(result.error);
              return;
            }
            setHistory((h) => [{ at: new Date().toISOString(), result: result.data }, ...h].slice(0, 10));
          });
        }}
      >
        <div>
          <label htmlFor={agentId} className="mb-1 block text-xs font-medium text-fg-muted">
            Agent
          </label>
          <select id={agentId} value={agent} onChange={(e) => setAgent(e.target.value)} className={`${inputClass} w-auto min-w-44`}>
            {agents.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label htmlFor={opId} className="mb-1 block text-xs font-medium text-fg-muted">
            Operation
          </label>
          <select
            id={opId}
            value={operation}
            onChange={(e) => setOperation(e.target.value as AgentOperation)}
            className={`${inputClass} w-auto min-w-52`}
          >
            {AGENT_OPERATIONS.map((op) => (
              <option key={op.value} value={op.value}>
                {op.label} ({op.scope})
              </option>
            ))}
          </select>
        </div>
        <button type="submit" className={buttonClass.primary} disabled={pending || !agent}>
          {pending ? "Requesting token…" : "Check permission"}
        </button>
      </form>
      <div aria-live="polite">{error ? <InlineError error={error} /> : null}</div>

      <ol aria-label="Permission check results" aria-live="polite" className="flex flex-col gap-2">
        {history.map(({ at, result }, i) => (
          <li
            key={`${at}-${i}`}
            className={`rounded-md border border-l-4 border-border p-3 ${result.allowed ? "border-l-impact-unaffected" : "border-l-impact-confirmed"}`}
          >
            <div className="flex flex-wrap items-center gap-2">
              {result.allowed ? (
                <Pill tone="ok">
                  <svg viewBox="0 0 16 16" width={10} height={10} aria-hidden="true">
                    <path d="m3.5 8.5 3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  Allowed
                </Pill>
              ) : (
                <Pill tone="danger">
                  <svg viewBox="0 0 16 16" width={10} height={10} aria-hidden="true">
                    <path d="m4 4 8 8M12 4l-8 8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                  Denied
                </Pill>
              )}
              <span className="text-sm text-fg">
                <Mono>{result.agent}</Mono> → <Mono>{result.operation}</Mono>
              </span>
              <span className="ml-auto text-[11px] text-fg-subtle">{at.slice(11, 19)} UTC</span>
            </div>
            <p className="mt-1.5 text-xs text-fg-muted">
              Required scope <Mono>{result.required_scope}</Mono>
            </p>
            <p className="mt-1 text-xs text-fg">{result.reason}</p>
          </li>
        ))}
      </ol>
    </div>
  );
}
