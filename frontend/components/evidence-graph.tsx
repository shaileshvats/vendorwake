"use client";

import { useId, useMemo, useState, type KeyboardEvent } from "react";

import { EvidenceCard, type RepoLinkInfo } from "@/components/evidence";
import { ImpactStatusBadge } from "@/components/impact-status-badge";
import { buttonClass } from "@/components/ui";
import type { EvidenceEdge, EvidenceGraph as Graph, EvidenceItem, EvidenceNode, EvidenceNodeType, ImpactStatus } from "@/types/contracts";

/*
 * Evidence graph drawn with plain SVG. Layout: one column per node type in pipeline order
 * (source → change → symbol → integration → file → test → risk), rows ordered by the barycenter of
 * each node's neighbours to reduce crossings. Edge style encodes what backs it: solid = deterministic
 * evidence, dashed = inference, dotted = no evidence item attached. Nodes are keyboard-focusable;
 * edges are reachable from the selected node's details. The evidence list below is the text alternative.
 */

const TYPE_ORDER: EvidenceNodeType[] = ["vendor_source", "change", "symbol", "integration", "file", "test", "risk"];
const TYPE_LABEL: Record<EvidenceNodeType, string> = {
  vendor_source: "Source",
  change: "Change",
  symbol: "Symbol",
  integration: "Integration",
  file: "File",
  test: "Test",
  risk: "Risk",
};

const COL_W = 196;
const NODE_W = 164;
const NODE_H = 46;
const ROW_H = NODE_H + 18;
const PAD_X = 16;
const HEADER_H = 30;
const PAD_Y = 12;

const STATUS_STROKE: Record<ImpactStatus, string> = {
  confirmed: "stroke-impact-confirmed",
  potential: "stroke-impact-potential",
  unaffected: "stroke-impact-unaffected",
  unknown: "stroke-impact-unknown",
};
const STATUS_FILL: Record<ImpactStatus, string> = {
  confirmed: "fill-impact-confirmed",
  potential: "fill-impact-potential",
  unaffected: "fill-impact-unaffected",
  unknown: "fill-impact-unknown",
};

interface Placed {
  node: EvidenceNode;
  x: number;
  y: number;
  col: number;
}

function typeRank(type: string): number {
  const index = TYPE_ORDER.indexOf(type as EvidenceNodeType);
  return index === -1 ? TYPE_ORDER.length : index;
}

function layout(graph: Graph) {
  const ranks = [...new Set(graph.nodes.map((n) => typeRank(n.type)))].sort((a, b) => a - b);
  const columns: EvidenceNode[][] = ranks.map((rank) => graph.nodes.filter((n) => typeRank(n.type) === rank));
  const colOf = new Map<string, number>();
  columns.forEach((nodes, col) => nodes.forEach((n) => colOf.set(n.id, col)));

  const neighbours = new Map<string, string[]>();
  for (const edge of graph.edges) {
    neighbours.set(edge.source, [...(neighbours.get(edge.source) ?? []), edge.target]);
    neighbours.set(edge.target, [...(neighbours.get(edge.target) ?? []), edge.source]);
  }

  // Two barycenter sweeps (left→right, right→left).
  const position = new Map<string, number>();
  columns.forEach((nodes) => nodes.forEach((n, i) => position.set(n.id, i)));
  const sweep = (order: number[], direction: 1 | -1) => {
    for (const col of order) {
      const scored = columns[col].map((node, i) => {
        const adj = (neighbours.get(node.id) ?? []).filter((other) => colOf.get(other) === col - direction);
        const score = adj.length ? adj.reduce((sum, other) => sum + (position.get(other) ?? 0), 0) / adj.length : i;
        return { node, score };
      });
      scored.sort((a, b) => a.score - b.score);
      columns[col] = scored.map((s) => s.node);
      columns[col].forEach((n, i) => position.set(n.id, i));
    }
  };
  const indices = columns.map((_, i) => i);
  sweep(indices.slice(1), 1);
  sweep(indices.slice(0, -1).reverse(), -1);

  const maxRows = Math.max(1, ...columns.map((c) => c.length));
  const height = HEADER_H + PAD_Y * 2 + maxRows * ROW_H - (ROW_H - NODE_H);
  const width = PAD_X * 2 + columns.length * COL_W - (COL_W - NODE_W);
  const placed = new Map<string, Placed>();
  columns.forEach((nodes, col) => {
    const offset = ((maxRows - nodes.length) * ROW_H) / 2;
    nodes.forEach((node, row) => {
      placed.set(node.id, { node, col, x: PAD_X + col * COL_W, y: HEADER_H + PAD_Y + offset + row * ROW_H });
    });
  });
  const headers = ranks.map((rank, col) => ({
    label: TYPE_LABEL[TYPE_ORDER[rank]] ?? "Other",
    x: PAD_X + col * COL_W + NODE_W / 2,
  }));
  return { placed, width, height, headers };
}

function edgePath(a: Placed, b: Placed): string {
  const ay = a.y + NODE_H / 2;
  const by = b.y + NODE_H / 2;
  if (b.col > a.col) {
    const x1 = a.x + NODE_W;
    const x2 = b.x;
    const dx = (x2 - x1) / 2;
    return `M${x1},${ay} C${x1 + dx},${ay} ${x2 - dx},${by} ${x2},${by}`;
  }
  if (b.col < a.col) {
    const x1 = a.x;
    const x2 = b.x + NODE_W;
    const dx = (x1 - x2) / 2;
    return `M${x1},${ay} C${x1 - dx},${ay} ${x2 + dx},${by} ${x2},${by}`;
  }
  // Same column: loop out to the right.
  const x = a.x + NODE_W;
  return `M${x},${ay} C${x + 36},${ay} ${x + 36},${by} ${x},${by}`;
}

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max - 1)}…` : text;
}

type Selection = { kind: "node"; id: string } | { kind: "edge"; index: number } | null;

function StatusGlyph({ status, x, y }: { status: ImpactStatus; x: number; y: number }) {
  // Same shapes as ImpactStatusBadge so status never depends on hue alone.
  const cls = `${STATUS_FILL[status]} ${STATUS_STROKE[status]}`;
  return (
    <g transform={`translate(${x},${y})`} aria-hidden="true">
      {status === "confirmed" ? <path d="M6 0 12 6 6 12 0 6Z" className={cls} /> : null}
      {status === "potential" ? <path d="M6 0.5 11.5 11H0.5Z" className={`${STATUS_STROKE[status]} fill-none`} strokeWidth={1.6} /> : null}
      {status === "unaffected" ? <path d="m1 6.5 3 3 7-7.5" className={`${STATUS_STROKE[status]} fill-none`} strokeWidth={1.8} /> : null}
      {status === "unknown" ? <circle cx="6" cy="6" r="4.5" className={`${STATUS_STROKE[status]} fill-none`} strokeDasharray="2 2" strokeWidth={1.4} /> : null}
    </g>
  );
}

export function EvidenceGraph({
  graph,
  evidence,
  repo,
}: {
  graph: Graph;
  evidence: EvidenceItem[];
  repo: RepoLinkInfo | null;
}) {
  const uid = useId().replace(/:/g, "");
  const { placed, width, height, headers } = useMemo(() => layout(graph), [graph]);
  const [selected, setSelected] = useState<Selection>(null);
  const [hovered, setHovered] = useState<Selection>(null);
  const active = hovered ?? selected;

  const nodeById = useMemo(() => new Map(graph.nodes.map((n) => [n.id, n])), [graph.nodes]);
  const drawable = graph.edges
    .map((edge, index) => ({ edge, index, a: placed.get(edge.source), b: placed.get(edge.target) }))
    .filter((e): e is { edge: EvidenceEdge; index: number; a: Placed; b: Placed } => Boolean(e.a && e.b));

  const isEdgeActive = (edge: EvidenceEdge, index: number) =>
    active?.kind === "edge" ? active.index === index : active?.kind === "node" ? edge.source === active.id || edge.target === active.id : false;
  const isNodeActive = (id: string) =>
    active?.kind === "node"
      ? active.id === id || graph.edges.some((e) => (e.source === active.id && e.target === id) || (e.target === active.id && e.source === id))
      : active?.kind === "edge"
        ? graph.edges[active.index]?.source === id || graph.edges[active.index]?.target === id
        : false;

  const evidenceFor = (edge: EvidenceEdge): EvidenceItem | null =>
    edge.evidence_index !== null && edge.evidence_index !== undefined ? (evidence[edge.evidence_index] ?? null) : null;

  const edgeStyle = (edge: EvidenceEdge) => {
    const item = evidenceFor(edge);
    if (!item) return "2 4";
    return item.deterministic ? undefined : "6 4";
  };

  function onNodeKey(event: KeyboardEvent, id: string) {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      setSelected((s) => (s?.kind === "node" && s.id === id ? null : { kind: "node", id }));
    } else if (event.key === "Escape") {
      setSelected(null);
    }
  }

  if (graph.nodes.length === 0) {
    return <p className="text-sm text-fg-muted">The assessment did not produce an evidence graph.</p>;
  }

  return (
    <div className="grid min-w-0 grid-cols-1 gap-3 xl:grid-cols-[minmax(0,1fr)_20rem]">
      <div className="min-w-0">
        <div className="overflow-x-auto rounded-md border border-border bg-canvas">
          <svg
            role="group"
            aria-label={`Evidence graph: ${graph.nodes.length} nodes, ${graph.edges.length} connections. Select a node for details; the evidence list below has the same information as text.`}
            width={width}
            height={height}
            viewBox={`0 0 ${width} ${height}`}
            className="block"
            onMouseLeave={() => setHovered(null)}
          >
            <defs>
              <marker id={`${uid}-arrow`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 Z" className="fill-fg-subtle" />
              </marker>
              <marker id={`${uid}-arrow-active`} viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                <path d="M0,0 L8,4 L0,8 Z" className="fill-accent" />
              </marker>
            </defs>

            {headers.map((h) => (
              <text key={h.label} x={h.x} y={18} textAnchor="middle" className="fill-fg-subtle text-[10px] font-medium uppercase tracking-wider">
                {h.label}
              </text>
            ))}

            <g aria-hidden="true">
              {drawable.map(({ edge, index, a, b }) => {
                const on = isEdgeActive(edge, index);
                const d = edgePath(a, b);
                return (
                  <g key={index}>
                    <path
                      d={d}
                      fill="none"
                      strokeWidth={on ? 2 : 1.3}
                      strokeDasharray={edgeStyle(edge)}
                      className={on ? "stroke-accent" : active ? "stroke-border-strong" : "stroke-fg-subtle"}
                      markerEnd={`url(#${uid}-${on ? "arrow-active" : "arrow"})`}
                    />
                    {/* Wide transparent hit area for pointer users. */}
                    <path
                      d={d}
                      fill="none"
                      stroke="transparent"
                      strokeWidth={12}
                      className="cursor-pointer"
                      onMouseEnter={() => setHovered({ kind: "edge", index })}
                      onMouseLeave={() => setHovered(null)}
                      onClick={() => setSelected({ kind: "edge", index })}
                    />
                  </g>
                );
              })}
            </g>

            {[...placed.values()].map(({ node, x, y }) => {
              const on = isNodeActive(node.id);
              const isSelected = selected?.kind === "node" && selected.id === node.id;
              const dim = active && !on;
              return (
                <g
                  key={node.id}
                  role="button"
                  tabIndex={0}
                  aria-pressed={isSelected}
                  aria-label={`${TYPE_LABEL[node.type] ?? node.type}: ${node.label}${node.status ? `, impact ${node.status}` : ""}`}
                  transform={`translate(${x},${y})`}
                  className="cursor-pointer outline-none [&:focus-visible>rect:first-child]:stroke-focus [&:focus-visible>rect:first-child]:[stroke-width:2.5]"
                  opacity={dim ? 0.45 : 1}
                  onMouseEnter={() => setHovered({ kind: "node", id: node.id })}
                  onMouseLeave={() => setHovered(null)}
                  onFocus={() => setHovered({ kind: "node", id: node.id })}
                  onBlur={() => setHovered(null)}
                  onClick={() => setSelected((s) => (s?.kind === "node" && s.id === node.id ? null : { kind: "node", id: node.id }))}
                  onKeyDown={(event) => onNodeKey(event, node.id)}
                >
                  <rect
                    width={NODE_W}
                    height={NODE_H}
                    rx={6}
                    className={`fill-surface ${node.status ? STATUS_STROKE[node.status] : on ? "stroke-accent" : "stroke-border-strong"}`}
                    strokeWidth={isSelected ? 2.5 : node.status ? 1.6 : 1}
                  />
                  <text x={10} y={17} className="fill-fg-subtle text-[9.5px] font-medium uppercase tracking-wider">
                    {TYPE_LABEL[node.type] ?? node.type}
                  </text>
                  <text x={10} y={34} className="fill-fg font-mono text-[11.5px]">
                    {truncate(node.label, node.status ? 19 : 22)}
                  </text>
                  {node.status ? <StatusGlyph status={node.status} x={NODE_W - 20} y={8} /> : null}
                  <title>{node.label}</title>
                </g>
              );
            })}
          </svg>
        </div>
        <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-fg-muted" aria-label="Graph legend">
          <li className="flex items-center gap-1.5">
            <svg width="26" height="6" aria-hidden="true"><path d="M0,3 H26" className="stroke-fg-subtle" strokeWidth="1.5" /></svg>
            Deterministic evidence
          </li>
          <li className="flex items-center gap-1.5">
            <svg width="26" height="6" aria-hidden="true"><path d="M0,3 H26" className="stroke-fg-subtle" strokeWidth="1.5" strokeDasharray="6 4" /></svg>
            Inference (model or search)
          </li>
          <li className="flex items-center gap-1.5">
            <svg width="26" height="6" aria-hidden="true"><path d="M0,3 H26" className="stroke-fg-subtle" strokeWidth="1.5" strokeDasharray="2 4" /></svg>
            No evidence item attached
          </li>
          <li>Node border and glyph show impact status.</li>
        </ul>
      </div>

      <aside aria-live="polite" aria-label="Graph selection details" className="rounded-md border border-border bg-surface p-3">
        <GraphDetails
          selection={active}
          graph={graph}
          nodeById={nodeById}
          evidenceFor={evidenceFor}
          repo={repo}
          onSelectEdge={(index) => {
            setHovered(null);
            setSelected({ kind: "edge", index });
          }}
          onSelectNode={(id) => {
            setHovered(null);
            setSelected({ kind: "node", id });
          }}
          onClear={() => {
            setHovered(null);
            setSelected(null);
          }}
          pinned={selected !== null && hovered === null}
        />
      </aside>
    </div>
  );
}

function GraphDetails({
  selection,
  graph,
  nodeById,
  evidenceFor,
  repo,
  onSelectEdge,
  onSelectNode,
  onClear,
  pinned,
}: {
  selection: Selection;
  graph: Graph;
  nodeById: Map<string, EvidenceNode>;
  evidenceFor: (edge: EvidenceEdge) => EvidenceItem | null;
  repo: RepoLinkInfo | null;
  onSelectEdge: (index: number) => void;
  onSelectNode: (id: string) => void;
  onClear: () => void;
  pinned: boolean;
}) {
  if (!selection) {
    return (
      <p className="text-sm text-fg-muted">
        Hover or select a node or connection to see the evidence behind it. Use Tab to move between nodes and Enter to select.
      </p>
    );
  }

  const labelOf = (id: string) => nodeById.get(id)?.label ?? id;

  if (selection.kind === "edge") {
    const edge = graph.edges[selection.index];
    if (!edge) return null;
    const item = evidenceFor(edge);
    return (
      <div className="flex flex-col gap-2 text-sm">
        <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">Connection</p>
        <p className="text-fg">
          <button type="button" className="font-mono text-xs text-accent hover:text-accent-strong" onClick={() => onSelectNode(edge.source)}>
            {labelOf(edge.source)}
          </button>{" "}
          <span className="text-fg-muted">{edge.label}</span>{" "}
          <button type="button" className="font-mono text-xs text-accent hover:text-accent-strong" onClick={() => onSelectNode(edge.target)}>
            {labelOf(edge.target)}
          </button>
        </p>
        {item && edge.evidence_index !== null && edge.evidence_index !== undefined ? (
          <>
            <EvidenceCard item={item} index={edge.evidence_index} repo={repo} />
            <a href={`#evidence-${edge.evidence_index}`} className="text-xs text-accent hover:text-accent-strong">
              Jump to E{edge.evidence_index} in the evidence list
            </a>
          </>
        ) : (
          <p className="text-xs text-fg-muted">No evidence item is attached to this connection.</p>
        )}
        {pinned ? (
          <button type="button" className={`${buttonClass.ghost} self-start`} onClick={onClear}>
            Clear selection
          </button>
        ) : null}
      </div>
    );
  }

  const node = nodeById.get(selection.id);
  if (!node) return null;
  const edges = graph.edges.map((edge, index) => ({ edge, index })).filter(({ edge }) => edge.source === node.id || edge.target === node.id);
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p className="text-xs font-medium uppercase tracking-wide text-fg-subtle">{TYPE_LABEL[node.type] ?? node.type}</p>
      <p className="break-all font-mono text-sm text-fg">{node.label}</p>
      {node.status ? <ImpactStatusBadge status={node.status} /> : null}
      <p className="mt-1 text-xs font-medium text-fg-muted">
        {edges.length} connection{edges.length === 1 ? "" : "s"}
      </p>
      <ul className="flex flex-col gap-1">
        {edges.map(({ edge, index }) => {
          const item = evidenceFor(edge);
          const outgoing = edge.source === node.id;
          return (
            <li key={index}>
              <button
                type="button"
                onClick={() => onSelectEdge(index)}
                className="w-full rounded border border-border px-2 py-1.5 text-left text-xs hover:border-border-strong"
              >
                <span className="text-fg-muted">{outgoing ? "→" : "←"} {edge.label}</span>{" "}
                <span className="font-mono text-fg">{labelOf(outgoing ? edge.target : edge.source)}</span>
                <span className="mt-0.5 block text-[11px] text-fg-subtle">
                  {item ? `E${edge.evidence_index} · ${item.deterministic ? "deterministic" : "inference"}` : "no evidence item"}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {pinned ? (
        <button type="button" className={`${buttonClass.ghost} self-start`} onClick={onClear}>
          Clear selection
        </button>
      ) : null}
    </div>
  );
}
