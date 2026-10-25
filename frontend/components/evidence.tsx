import { ExternalLink, Mono, Pill } from "@/components/ui";
import { githubFileUrl, humanize } from "@/lib/format";
import type { EvidenceItem, EvidenceKind } from "@/types/contracts";

/** Enough of a repository to build GitHub line links. */
export interface RepoLinkInfo {
  html_url: string;
  ref: string;
  subpath: string;
  commit_sha: string | null;
}

const KIND_LABEL: Record<EvidenceKind, string> = {
  vendor_source: "Vendor source",
  search_result: "Search result",
  dependency: "Dependency",
  code_reference: "Code reference",
  version_check: "Version check",
  test_result: "Test result",
  model_inference: "Model inference",
};

export function evidenceKindLabel(kind: string): string {
  return KIND_LABEL[kind as EvidenceKind] ?? humanize(kind);
}

export function DeterminismPill({ deterministic }: { deterministic: boolean }) {
  return deterministic ? (
    <Pill tone="ok" title="Produced by a deterministic check (static analysis, version comparison, test run or hashed source).">
      Deterministic
    </Pill>
  ) : (
    <Pill tone="warn" title="Produced by a model or a search engine. Never sufficient on its own for Confirmed.">
      Inference
    </Pill>
  );
}

export function FileRef({ repo, path, line }: { repo: RepoLinkInfo | null; path: string; line?: number | null }) {
  const text = `${path}${line ? `:${line}` : ""}`;
  const href = repo ? githubFileUrl(repo, path, line) : null;
  return href ? (
    <ExternalLink href={href} className="font-mono text-xs">
      {text}
    </ExternalLink>
  ) : (
    <Mono>{text}</Mono>
  );
}

/** One evidence item with its provenance (file/line or URL + hash). */
export function EvidenceCard({ item, index, repo }: { item: EvidenceItem; index: number; repo: RepoLinkInfo | null }) {
  return (
    <div className="text-sm">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono text-[11px] text-fg-subtle">E{index}</span>
        <Pill>{evidenceKindLabel(item.kind)}</Pill>
        <DeterminismPill deterministic={item.deterministic} />
        {item.kind === "search_result" ? <Pill>Discovery, not proof</Pill> : null}
      </div>
      <p className="mt-1.5 text-fg">{item.description}</p>
      {item.quote ? (
        <blockquote className="mt-1.5 whitespace-pre-wrap border-l-2 border-border-strong pl-3 font-mono text-xs leading-relaxed text-fg-muted">
          {item.quote}
        </blockquote>
      ) : null}
      <div className="mt-1.5 flex flex-col gap-0.5 text-xs text-fg-muted">
        {item.file_path ? (
          <span>
            File: <FileRef repo={repo} path={item.file_path} line={item.line} />
          </span>
        ) : null}
        {item.source_url ? (
          <span className="break-all">
            Source: <ExternalLink href={item.source_url}>{item.source_url}</ExternalLink>
          </span>
        ) : null}
        {item.content_sha256 ? (
          <span>
            SHA-256: <Mono>{item.content_sha256}</Mono>
          </span>
        ) : null}
      </div>
    </div>
  );
}
