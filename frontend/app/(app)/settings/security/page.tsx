import type { Metadata } from "next";

import { ApiErrorState } from "@/components/api-error";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";
import { PermissionTester } from "@/components/permission-tester";
import { SettingsTabs } from "@/components/settings-tabs";
import { Mono, tableClass, tdClass, thClass } from "@/components/ui";
import { AGENT_OPERATIONS } from "@/lib/agent-operations";
import { listAgents } from "@/lib/vendorwake";

export const metadata: Metadata = { title: "Security" };

export default async function SecurityPage() {
  const agents = await listAgents();

  return (
    <>
      <PageHeader
        title="Settings"
        description="Your account, agent identities, and the permissions each agent holds."
      />
      <SettingsTabs current="/settings/security" />

      <div className="grid grid-cols-1 gap-4">
        <Panel
          title="Agent identities"
          description="Each Vendorwake agent is its own Auth0 machine-to-machine client with only the API scopes it needs."
        >
          {!agents.ok ? (
            <ApiErrorState error={agents.error} resource="the agent list" />
          ) : agents.data.length === 0 ? (
            <EmptyState title="No agent identities configured." description="The API reports no Auth0 agent clients." />
          ) : (
            <div className="-mx-4 overflow-x-auto">
              <table className={tableClass}>
                <caption className="sr-only">Agent identities and the scopes each holds</caption>
                <thead>
                  <tr>
                    <th scope="col" className={thClass}>Agent</th>
                    <th scope="col" className={thClass}>Auth0 client ID</th>
                    {AGENT_OPERATIONS.map((op) => (
                      <th key={op.scope} scope="col" className={`${thClass} text-center normal-case`}>
                        <Mono className="text-fg-subtle">{op.scope}</Mono>
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {agents.data.map((agent) => (
                    <tr key={agent.agent}>
                      <th scope="row" className={`${tdClass} font-medium text-fg`}>
                        {agent.agent}
                      </th>
                      <td className={tdClass}>
                        <Mono>{agent.client_id}</Mono>
                      </td>
                      {AGENT_OPERATIONS.map((op) => {
                        const held = agent.scopes.includes(op.scope);
                        return (
                          <td key={op.scope} className={`${tdClass} text-center text-xs`}>
                            {held ? (
                              <span className="text-impact-unaffected">
                                <span aria-hidden="true">● </span>granted
                              </span>
                            ) : (
                              <span className="text-fg-subtle">
                                <span aria-hidden="true">○ </span>not granted
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
              {agents.data.some((a) => a.scopes.some((s) => !AGENT_OPERATIONS.some((op) => op.scope === s))) ? (
                <p className="mt-2 px-4 text-xs text-fg-muted">
                  Other scopes:{" "}
                  {agents.data.map((a) => {
                    const extra = a.scopes.filter((s) => !AGENT_OPERATIONS.some((op) => op.scope === s));
                    return extra.length ? (
                      <span key={a.agent} className="mr-3">
                        {a.agent}: <Mono>{extra.join(" ")}</Mono>
                      </span>
                    ) : null;
                  })}
                </p>
              ) : null}
            </div>
          )}
        </Panel>

        <Panel
          title="Permission tester"
          description="Requests a real token for the agent from Auth0 and checks it against the scope the operation requires. Denials are logged by the API."
        >
          {agents.ok && agents.data.length > 0 ? (
            <>
              <PermissionTester agents={agents.data.map((a) => a.agent)} />
              <p className="mt-3 border-t border-border pt-3 text-xs text-fg-muted">
                Pushing a branch requires <Mono>repo:write</Mono>, which no agent holds, and additionally a human approval in the
                investigation.
              </p>
            </>
          ) : (
            <EmptyState title="No agents to test." description="Agent identities must load before permissions can be checked." />
          )}
        </Panel>
      </div>
    </>
  );
}
