import { EmptyState } from "@/components/empty-state";
import { Mono, Pill, tableClass, tdClass, thClass } from "@/components/ui";
import { formatNumber, formatSeconds } from "@/lib/format";
import type { ModelCallInfo } from "@/types/contracts";

/** Provider-reported usage for each model call. Missing token counts stay "—", never estimated. */
export function ModelCallsTable({ calls }: { calls: ModelCallInfo[] }) {
  if (calls.length === 0) return <EmptyState title="No model calls recorded." />;
  const totalIn = calls.reduce((sum, c) => sum + (c.input_tokens ?? 0), 0);
  const totalOut = calls.reduce((sum, c) => sum + (c.output_tokens ?? 0), 0);
  const totalLatency = calls.reduce((sum, c) => sum + c.latency_s, 0);
  return (
    <div className="-mx-4 overflow-x-auto">
      <table className={tableClass}>
        <caption className="sr-only">Model calls</caption>
        <thead>
          <tr>
            <th scope="col" className={thClass}>Agent</th>
            <th scope="col" className={thClass}>Model</th>
            <th scope="col" className={`${thClass} text-right`}>Input tokens</th>
            <th scope="col" className={`${thClass} text-right`}>Output tokens</th>
            <th scope="col" className={`${thClass} text-right`}>Latency</th>
          </tr>
        </thead>
        <tbody>
          {calls.map((call, i) => (
            <tr key={`${call.agent}-${i}`}>
              <th scope="row" className={`${tdClass} font-normal`}>
                {call.agent}
                {call.repaired ? (
                  <span className="ml-1.5">
                    <Pill tone="warn" title="The first output failed schema validation; a repair call fixed it.">
                      repaired
                    </Pill>
                  </span>
                ) : null}
              </th>
              <td className={tdClass}>
                <Mono>{call.model}</Mono>
                <p className="text-xs text-fg-subtle">via {call.provider}</p>
              </td>
              <td className={`${tdClass} text-right tabular-nums`}>{formatNumber(call.input_tokens)}</td>
              <td className={`${tdClass} text-right tabular-nums`}>{formatNumber(call.output_tokens)}</td>
              <td className={`${tdClass} text-right tabular-nums`}>{formatSeconds(call.latency_s)}</td>
            </tr>
          ))}
        </tbody>
        {calls.length > 1 ? (
          <tfoot>
            <tr>
              <th scope="row" colSpan={2} className={`${tdClass} text-xs font-medium text-fg-muted`}>
                Total ({calls.length} calls)
              </th>
              <td className={`${tdClass} text-right tabular-nums text-fg-muted`}>{formatNumber(totalIn)}</td>
              <td className={`${tdClass} text-right tabular-nums text-fg-muted`}>{formatNumber(totalOut)}</td>
              <td className={`${tdClass} text-right tabular-nums text-fg-muted`}>{formatSeconds(totalLatency)}</td>
            </tr>
          </tfoot>
        ) : null}
      </table>
    </div>
  );
}
