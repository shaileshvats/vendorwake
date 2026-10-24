import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { AppShell } from "@/components/app-shell";
import { getCurrentUser } from "@/lib/session";

/** Authenticated area. Signed-out visitors are sent to the landing page. */
export default async function AuthenticatedLayout({ children }: { children: ReactNode }) {
  const user = await getCurrentUser();
  if (!user) redirect("/");

  return <AppShell user={user}>{children}</AppShell>;
}
