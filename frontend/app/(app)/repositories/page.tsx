import type { Metadata } from "next";

import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { Panel } from "@/components/panel";

export const metadata: Metadata = { title: "Repositories" };

export default function RepositoriesPage() {
  return (
    <>
      <PageHeader
        title="Repositories"
        description="GitHub repositories you connect, with the vendor SDKs and API calls Vendorwake finds in each."
      />
      <Panel title="Connected repositories">
        <EmptyState
          title="No repositories connected yet."
          description="Repository connection is not available yet. Connected repositories will be listed here with their detected vendor integrations."
        />
      </Panel>
    </>
  );
}
