"use client";

import { useRouter } from "next/navigation";
import { useId, useState, useTransition } from "react";

import { InlineError } from "@/components/api-error";
import { buttonClass, inputClass } from "@/components/ui";
import { investigateChangeAction, type ActionError } from "@/lib/actions";

export interface RepoOption {
  id: string;
  label: string;
  ready: boolean;
}

/** "Investigate against repo…": starts an investigation workflow and opens it. */
export function InvestigateForm({ changeId, repositories }: { changeId: string; repositories: RepoOption[] }) {
  const router = useRouter();
  const selectId = useId();
  const firstReady = repositories.find((r) => r.ready) ?? repositories[0];
  const [repositoryId, setRepositoryId] = useState(firstReady?.id ?? "");
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <form
      className="flex flex-col gap-2"
      aria-busy={pending}
      onSubmit={(event) => {
        event.preventDefault();
        setError(null);
        startTransition(async () => {
          const result = await investigateChangeAction(changeId, repositoryId);
          if (!result.ok) {
            setError(result.error);
            return;
          }
          router.push(`/investigations/${result.data.investigation_id}`);
        });
      }}
    >
      <label htmlFor={selectId} className="text-xs font-medium text-fg-muted">
        Repository
      </label>
      <div className="flex flex-wrap gap-2">
        <select
          id={selectId}
          value={repositoryId}
          onChange={(event) => setRepositoryId(event.target.value)}
          className={`${inputClass} w-auto min-w-56 flex-1`}
          required
        >
          {repositories.map((repo) => (
            <option key={repo.id} value={repo.id}>
              {repo.label}
              {repo.ready ? "" : " (analysis not complete)"}
            </option>
          ))}
        </select>
        <button type="submit" className={buttonClass.primary} disabled={pending || !repositoryId}>
          {pending ? "Starting investigation…" : "Investigate"}
        </button>
      </div>
      <div aria-live="polite">{error ? <InlineError error={error} /> : null}</div>
    </form>
  );
}
