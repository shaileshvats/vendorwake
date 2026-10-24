export function Logo({ className = "" }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 font-semibold tracking-tight text-fg ${className}`}>
      <svg viewBox="0 0 32 32" width={22} height={22} aria-hidden="true" focusable="false">
        <rect width="32" height="32" rx="7" className="fill-surface-raised" />
        <path
          d="M7 11l6 12 3-6 3 6 6-12"
          fill="none"
          className="stroke-accent"
          strokeWidth="2.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      Vendorwake
    </span>
  );
}
