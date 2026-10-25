"use client";

import { useRouter } from "next/navigation";
import { useId, useState, useTransition } from "react";

import { InlineError } from "@/components/api-error";
import { buttonClass, inputClass } from "@/components/ui";
import { connectRepositoryAction, type ActionError } from "@/lib/actions";

const DEMO = { owner: "shaileshvats", name: "vendorwake", ref: "main", subpath: "fixtures/stripe-billing-demo" };

export function ConnectRepositoryForm() {
  const router = useRouter();
  const formId = useId();
  const [values, setValues] = useState({ owner: "", name: "", ref: "main", subpath: "" });
  const [error, setError] = useState<ActionError | null>(null);
  const [pending, startTransition] = useTransition();

  function submit(input: typeof values) {
    setError(null);
    startTransition(async () => {
      const result = await connectRepositoryAction(input);
      if (!result.ok) {
        setError(result.error);
        return;
      }
      router.push(`/repositories/${result.data.id}`);
    });
  }

  const field = (key: keyof typeof values, label: string, placeholder: string, required = false) => (
    <div>
      <label htmlFor={`${formId}-${key}`} className="mb-1 block text-xs font-medium text-fg-muted">
        {label}
        {required ? <span aria-hidden="true"> *</span> : <span className="text-fg-subtle"> (optional)</span>}
      </label>
      <input
        id={`${formId}-${key}`}
        name={key}
        required={required}
        value={values[key]}
        placeholder={placeholder}
        autoComplete="off"
        spellCheck={false}
        onChange={(event) => setValues((v) => ({ ...v, [key]: event.target.value }))}
        className={`${inputClass} font-mono`}
      />
    </div>
  );

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        submit(values);
      }}
      className="flex flex-col gap-3"
      aria-busy={pending}
    >
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {field("owner", "Owner", "octo-org", true)}
        {field("name", "Repository", "billing-service", true)}
        {field("ref", "Ref", "main")}
        {field("subpath", "Subpath", "services/billing")}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <button type="submit" className={buttonClass.primary} disabled={pending}>
          {pending ? "Connecting…" : "Connect and analyze"}
        </button>
        <button
          type="button"
          className={buttonClass.secondary}
          disabled={pending}
          onClick={() => {
            setValues(DEMO);
            submit(DEMO);
          }}
        >
          Use demo fixture
        </button>
        <span className="text-xs text-fg-subtle">
          <code className="font-mono">
            {DEMO.owner}/{DEMO.name}
          </code>{" "}
          · subpath <code className="font-mono">{DEMO.subpath}</code>
        </span>
      </div>
      <div aria-live="polite">{error ? <InlineError error={error} /> : null}</div>
    </form>
  );
}
