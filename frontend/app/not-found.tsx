import Link from "next/link";

export default function NotFound() {
  return (
    <main id="main" className="mx-auto flex min-h-screen max-w-md flex-col justify-center px-4">
      <p className="font-mono text-xs text-fg-subtle">404</p>
      <h1 className="mt-1 text-xl font-semibold text-fg">Page not found</h1>
      <p className="mt-2 text-sm text-fg-muted">The page you requested does not exist.</p>
      <Link href="/" className="mt-4 text-sm font-medium text-accent hover:text-accent-strong">
        Go to Vendorwake
      </Link>
    </main>
  );
}
