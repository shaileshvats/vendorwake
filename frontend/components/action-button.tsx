"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import { InlineError } from "@/components/api-error";
import { buttonClass } from "@/components/ui";
import type { ActionError, ActionResult } from "@/lib/actions";

/**
 * Runs a (bound) Server Action, then refreshes the server-rendered page. If the result carries a
 * `workflow_id`, it is shown so the user can find the run in Temporal.
 */
export function ActionButton({
  action,
  label,
  pendingLabel,
  successMessage,
  variant = "secondary",
  disabled = false,
  describedBy,
}: {
  action: () => Promise<ActionResult<unknown>>;
  label: string;
  pendingLabel?: string;
  successMessage?: string;
  variant?: keyof typeof buttonClass;
  disabled?: boolean;
  describedBy?: string;
}) {
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  const [error, setError] = useState<ActionError | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        className={buttonClass[variant]}
        disabled={disabled || pending}
        aria-describedby={describedBy}
        onClick={() => {
          setError(null);
          setMessage(null);
          startTransition(async () => {
            const result = await action();
            if (!result.ok) {
              setError(result.error);
              return;
            }
            const data = result.data as Record<string, unknown> | null;
            const workflowId = data && typeof data.workflow_id === "string" ? data.workflow_id : null;
            setMessage([successMessage, workflowId ? `Workflow ${workflowId}` : null].filter(Boolean).join(" · ") || null);
            router.refresh();
          });
        }}
      >
        {pending ? (pendingLabel ?? `${label}…`) : label}
      </button>
      <div aria-live="polite" className="max-w-xs">
        {error ? <InlineError error={error} /> : null}
        {message && !error ? <p className="text-xs text-fg-muted">{message}</p> : null}
      </div>
    </div>
  );
}
