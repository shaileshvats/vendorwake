"use client";

import { useId, useState, useTransition } from "react";

import { InlineError } from "@/components/api-error";
import { buttonClass, inputClass, Mono } from "@/components/ui";
import { askInvestigationAction, type ActionError } from "@/lib/actions";
import type { AskResponse } from "@/types/resources";

interface Turn {
  question: string;
  response: AskResponse;
}

/** Follow-up questions answered from the investigation's stored context (Backboard memory). */
export function AskBox({ investigationId }: { investigationId: string }) {
  const inputId = useId();
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState<Turn[]>([]);
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  return (
    <div className="flex flex-col gap-3">
      <form
        aria-busy={pending}
        onSubmit={(event) => {
          event.preventDefault();
          const asked = question.trim();
          if (!asked) return;
          setError(null);
          startTransition(async () => {
            const result = await askInvestigationAction(investigationId, asked);
            if (!result.ok) {
              setError(result.error);
              return;
            }
            setTurns((t) => [{ question: asked, response: result.data }, ...t]);
            setQuestion("");
          });
        }}
        className="flex flex-col gap-2"
      >
        <label htmlFor={inputId} className="text-xs font-medium text-fg-muted">
          Ask a follow-up question about this investigation
        </label>
        <textarea
          id={inputId}
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) event.currentTarget.form?.requestSubmit();
          }}
          rows={2}
          maxLength={2000}
          placeholder="e.g. Which call sites break if we upgrade without the patch?"
          className={`${inputClass} resize-y`}
        />
        <div className="flex items-center gap-2">
          <button type="submit" className={buttonClass.primary} disabled={pending || !question.trim()}>
            {pending ? "Asking…" : "Ask"}
          </button>
          <span className="text-[11px] text-fg-subtle">Ctrl/⌘ + Enter to send</span>
        </div>
      </form>
      <div aria-live="polite">{error ? <InlineError error={error} /> : null}</div>
      {pending ? (
        <p role="status" className="text-xs text-fg-muted">
          Retrieving context and generating an answer…
        </p>
      ) : null}

      <ol className="flex flex-col gap-3" aria-label="Answers">
        {turns.map((turn, i) => (
          <li key={turns.length - i} className="rounded-md border border-border p-3">
            <p className="text-xs font-medium text-fg-muted">Q: {turn.question}</p>
            <p className="mt-2 whitespace-pre-wrap text-sm text-fg">{turn.response.answer}</p>
            <p className="mt-2 text-[11px] text-fg-subtle">
              Model <Mono>{turn.response.model}</Mono> · model-generated answer, check it against the evidence
            </p>
            <details className="mt-2" open={turn.response.retrieved_memories.length > 0 && i === 0}>
              <summary className="cursor-pointer text-xs font-medium text-fg-muted">
                Retrieved memories used ({turn.response.retrieved_memories.length})
              </summary>
              {turn.response.retrieved_memories.length === 0 ? (
                <p className="mt-1 text-xs text-fg-subtle">No stored memories were retrieved for this answer.</p>
              ) : (
                <ul className="mt-1.5 flex flex-col gap-1.5">
                  {turn.response.retrieved_memories.map((memory) => (
                    <li key={memory.id} className="rounded border border-border bg-canvas px-2 py-1.5 text-xs">
                      <p className="text-fg">{memory.memory}</p>
                      <p className="mt-0.5 text-[11px] text-fg-subtle">
                        score {memory.score === null || memory.score === undefined ? "—" : memory.score.toFixed(3)} · id{" "}
                        <Mono>{memory.id}</Mono>
                      </p>
                    </li>
                  ))}
                </ul>
              )}
            </details>
          </li>
        ))}
      </ol>
    </div>
  );
}
