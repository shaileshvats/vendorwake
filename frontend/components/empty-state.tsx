import type { ReactNode } from "react";

/** Honest "nothing here yet" state. Never render placeholder data in its place. */
export function EmptyState({ title, description, children }: { title: string; description?: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-border-strong px-4 py-6 text-center">
      <p className="text-sm font-medium text-fg">{title}</p>
      {description ? <p className="mx-auto mt-1 max-w-md text-xs text-fg-muted">{description}</p> : null}
      {children ? <div className="mt-3">{children}</div> : null}
    </div>
  );
}
