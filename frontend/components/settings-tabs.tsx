import Link from "next/link";

const TABS = [
  { href: "/settings", label: "General" },
  { href: "/settings/security", label: "Security" },
] as const;

export function SettingsTabs({ current }: { current: (typeof TABS)[number]["href"] }) {
  return (
    <nav aria-label="Settings sections" className="mb-4 border-b border-border">
      <ul className="-mb-px flex gap-1">
        {TABS.map((tab) => (
          <li key={tab.href}>
            <Link
              href={tab.href}
              aria-current={tab.href === current ? "page" : undefined}
              className={`inline-block border-b-2 px-3 py-2 text-sm ${
                tab.href === current ? "border-accent font-medium text-fg" : "border-transparent text-fg-muted hover:text-fg"
              }`}
            >
              {tab.label}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
