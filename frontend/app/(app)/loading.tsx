export default function Loading() {
  return (
    <div role="status" className="flex items-center gap-2 py-10 text-sm text-fg-muted">
      <svg viewBox="0 0 16 16" width={14} height={14} aria-hidden="true" className="animate-spin text-accent motion-reduce:animate-none">
        <path d="M8 2a6 6 0 1 1-6 6" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
      Loading from the Vendorwake API…
    </div>
  );
}
