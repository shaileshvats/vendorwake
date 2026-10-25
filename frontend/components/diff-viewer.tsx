/**
 * Unified-diff viewer. Parses hunk headers to show old/new line numbers; additions and deletions keep
 * their +/- marker so they are distinguishable without color.
 */

type Row =
  | { type: "file"; text: string }
  | { type: "hunk"; text: string }
  | { type: "add" | "del" | "ctx" | "meta"; text: string; oldNo: number | null; newNo: number | null };

const HUNK_RE = /^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/;

function parse(patch: string): Row[] {
  const rows: Row[] = [];
  let oldNo = 0;
  let newNo = 0;
  // Lines left in the current hunk; outside a hunk, "--- "/"+++ " are file headers.
  let oldLeft = 0;
  let newLeft = 0;
  for (const line of patch.replace(/\r\n/g, "\n").replace(/\n$/, "").split("\n")) {
    const inHunk = oldLeft > 0 || newLeft > 0;
    const hunk = HUNK_RE.exec(line);
    if (hunk) {
      oldNo = Number(hunk[1]);
      oldLeft = hunk[2] === undefined ? 1 : Number(hunk[2]);
      newNo = Number(hunk[3]);
      newLeft = hunk[4] === undefined ? 1 : Number(hunk[4]);
      rows.push({ type: "hunk", text: line });
    } else if (!inHunk && (/^(diff |index |--- |\+\+\+ )/.test(line) || !/^[-+ \\]/.test(line))) {
      // Outside a hunk (or past its declared length, which model-written patches often get wrong,
      // so +/- lines still render as changes below).
      rows.push({ type: "file", text: line });
    } else if (line.startsWith("+")) {
      rows.push({ type: "add", text: line, oldNo: null, newNo: newNo++ });
      newLeft--;
    } else if (line.startsWith("-")) {
      rows.push({ type: "del", text: line, oldNo: oldNo++, newNo: null });
      oldLeft--;
    } else if (line.startsWith("\\")) {
      rows.push({ type: "meta", text: line, oldNo: null, newNo: null });
    } else {
      rows.push({ type: "ctx", text: line, oldNo: oldNo++, newNo: newNo++ });
      oldLeft--;
      newLeft--;
    }
  }
  return rows;
}

const ROW_CLASS: Record<Row["type"], string> = {
  file: "text-fg-muted",
  hunk: "bg-accent/10 text-accent",
  add: "bg-impact-unaffected/12 text-fg",
  del: "bg-impact-confirmed/12 text-fg",
  ctx: "text-fg-muted",
  meta: "text-fg-subtle italic",
};

export function DiffViewer({ patch, label }: { patch: string; label: string }) {
  const rows = parse(patch);
  const added = rows.filter((r) => r.type === "add").length;
  const removed = rows.filter((r) => r.type === "del").length;
  return (
    <figure className="overflow-hidden rounded-md border border-border">
      <figcaption className="flex items-center justify-between border-b border-border bg-surface-raised px-3 py-1.5 text-xs text-fg-muted">
        <span>{label}</span>
        <span className="font-mono">
          <span className="text-impact-unaffected">+{added}</span> <span className="text-impact-confirmed">−{removed}</span>
        </span>
      </figcaption>
      <div className="max-h-[28rem] overflow-auto bg-canvas" tabIndex={0} aria-label={`${label}, ${added} additions, ${removed} deletions`}>
        <table className="w-full border-collapse font-mono text-xs leading-5">
          <thead className="sr-only">
            <tr>
              <th scope="col">Old line</th>
              <th scope="col">New line</th>
              <th scope="col">Content</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) =>
              row.type === "file" || row.type === "hunk" ? (
                <tr key={i} className={ROW_CLASS[row.type]}>
                  <td colSpan={3} className="whitespace-pre px-3">
                    {row.text}
                  </td>
                </tr>
              ) : (
                <tr key={i} className={ROW_CLASS[row.type]}>
                  <td className="w-10 select-none border-r border-border px-2 text-right text-fg-subtle tabular-nums">{row.oldNo ?? ""}</td>
                  <td className="w-10 select-none border-r border-border px-2 text-right text-fg-subtle tabular-nums">{row.newNo ?? ""}</td>
                  <td className="whitespace-pre px-3">{row.text || " "}</td>
                </tr>
              ),
            )}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
