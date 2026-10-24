import { useId, type ReactNode } from "react";

/** A titled section card. Renders as a labelled <section> landmark. */
export function Panel({
  title,
  description,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  children: ReactNode;
  className?: string;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className={`rounded-lg border border-border bg-surface ${className}`}>
      <header className="border-b border-border px-4 py-3">
        <h2 id={headingId} className="text-sm font-semibold text-fg">
          {title}
        </h2>
        {description ? <p className="mt-0.5 text-xs text-fg-muted">{description}</p> : null}
      </header>
      <div className="p-4">{children}</div>
    </section>
  );
}
