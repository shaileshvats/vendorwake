---
title: "Vendorwake: the upstream changed, Gemma caught it, and a test proved it"
published: false
tags: devchallenge, hf26challenge, opensource, ai
---

*This is a submission for the [DEV Hacktoberfest 2026 Week 3 Challenge: Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19).*

## What I Built

Every app I've shipped depends on a dozen vendors I don't control. They deprecate parameters, rename
classes and cut major versions, and I usually find out when production does.

**Vendorwake** is the lazy fix: an open-source engineer that reads my vendors' changelogs so I don't have
to, and only bothers me when a change actually reaches my code. It doesn't just say "Stripe shipped
16.0.0." It says:

> `billing/invoices.py:10` calls `invoice.request(...)`, which 16.0.0 removed.
> **Confirmed:** the fixture's tests pass on 15.6.1 and fail on 16.0.0
> (`AttributeError: request. Did you mean: '_request'?`). Here are the proposed patches, and here is what
> happened when I ran them.

Every finding carries one of three labels, and the label means something:

- **Confirmed**: deterministic proof. An exact symbol reference in my code *and* a removed or renamed API,
  or a test that fails under the new version.
- **Potential**: a plausible connection the model found, waiting for proof.
- **Unaffected**: the vendor or symbol isn't used.

The model can explain and downgrade a finding, but **it cannot upgrade one**. That rule is enforced in code,
not in the prompt.

## Demo

- **Live app:** https://vendorwake-web.onrender.com (free Render tier: the first request after a quiet
  period takes about a minute while the service wakes up)
- **API docs:** https://vendorwake-api.onrender.com/api/docs

The demo investigation is a real upstream change: **stripe-python 16.0.0** (released 2026-09-30) against a
small billing fixture pinned to 15.6.1. All three breaking changes reproduce offline:

| Changelog item | 15.6.1 | 16.0.0 |
|---|---|---|
| `stripe.Reversal` → `stripe.TransferReversal` | exists | `AttributeError` |
| `StripeObject.request()` removed | exists | `AttributeError` |
| Path params positional-only | keyword accepted | `TypeError: ... positional-only arguments passed as keyword` |

**What the hosted run actually produced** (one investigation, end to end, on free tiers):

1. **Repository analysis** found `stripe==15.6.1` in `requirements.txt` (95% detection confidence) and
   every reference by file and line: `stripe.Reversal` in `payouts.py`, `invoice.request(...)` in
   `invoices.py`, `customers.retrieve(customer=...)` in `customers.py`, plus `catalog.py`, which uses
   Stripe but none of the affected APIs.
2. **Discovery** ran two SerpApi searches, fetched the official changelog, hashed it into a TimescaleDB
   snapshot, and had Gemma 4 extract **41 changes** from the 16.0.0 release. The grounding check accepted
   the important ones with verbatim quotes, and flagged **4 quotes and 10 symbols** that Gemma had reworded
   (for example, it glued a class path onto a field name). So the change is marked *needs human review*
   instead of pretending to be clean.
3. **Version comparison:** in a fresh virtualenv, the fixture's tests **passed on 15.6.1 and failed on
   16.0.0**. That failing test is the deterministic evidence behind **Confirmed** (5 deterministic
   evidence items and 1 model inference on the `invoices.py` finding).
4. **Patches:** Gemma proposed one diff per affected file. The `stripe.Reversal` →
   `stripe.TransferReversal` diff is exactly right. Then the sandbox ran the tests with every patch applied
   on 16.0.0: **3 passed, 1 failed**. Gemma had replaced `invoice.request(...)` with `raw_request(...)`,
   which returns a raw `StripeResponse`, so `sent.id` blew up. The recommendation is labelled
   **"Verified: failed"**, and nothing is applied: approving a patch only records the decision.
5. **Follow-up:** I asked "Which file breaks first if we upgrade without any patch?" Backboard retrieved
   the investigation's memory, and Gemma answered that the context doesn't say. It declined to make up an
   ordering, and the UI shows the one memory it used.
6. **Provenance:** a Sentry trace with **315 spans** (version comparison 2.8 min, assessment 1.3 min,
   patch proposal 7.4 min on Google's free tier), an Entire checkpoint linked to the migration agent's
   commit, and the Temporal workflow and run IDs. All are linked from the investigation page.
7. **Permissions:** on Settings → Security, the migration agent asking to `push_branch` is **denied**
   (`repo:write` missing), and `propose_patch` is **allowed**. Both use real Auth0 tokens.

One flaw I'm leaving visible: the model-written *summary* mentions a `KeyError` that no test produced. The
verdicts come from deterministic evidence, but the narrative is still model text, so the page labels it as
such.

## Code

{% embed https://github.com/shaileshvats/vendorwake %}

Apache-2.0. FastAPI + Temporal + PostgreSQL/TimescaleDB/pgvector + Next.js, with an adapter per sponsor so
the business logic never imports a vendor SDK.

## How I Built It

**Deterministic code builds the evidence; the open-weight model interprets it.**

1. **Discovery.** SerpApi finds candidate announcements. A search hit is *never* proof: Vendorwake only
   trusts text it fetched itself from the vendor's official domains. It uses plain HTTP first and a
   Browserbase browser when the page is a JavaScript shell. Each fetch is hashed and stored in a
   TimescaleDB hypertable, so unchanged pages are skipped and every finding points at the exact bytes
   it came from.
2. **Extraction.** Gemma 4 turns a release section into structured changes: type, severity, affected
   symbols, replacement and a verbatim evidence quote. Then a **grounding check** throws out any quote that
   isn't literally in the source. Vendor text is wrapped as untrusted data, and a planted "ignore previous
   instructions" line in the tests gets flagged instead of obeyed.
3. **Repository analysis.** Manifest parsers plus a Python AST scanner find imports, attribute chains like
   `stripe.Reversal`, and keyword calls like `.retrieve(customer=...)`, each with a file and line.
4. **Impact.** Rules match extracted symbols to code references and version ranges. Gemma writes the
   rationale and builds an **evidence graph** (vendor source → change → symbol → file → test) where every
   edge points at a piece of evidence.
5. **Migration and verification.** Gemma proposes a unified diff per affected file. Vendorwake checks that
   the diff applies, then runs the fixture's tests in a fresh virtualenv before the upgrade, after it, and
   after the patch. Patches are never pushed: no agent identity holds `repo:write`.

**The sponsors each do one real job:**

- **Gemma 4 (open-weight):** extraction, impact rationale, patches and follow-up answers. It runs on
  Google AI Studio's free tier through an OpenAI-compatible adapter, so swapping in vLLM or Ollama is a
  config change.
- **Temporal:** durable workflows for analysis, discovery and investigation. Every step persists its
  output before returning, so a restarted worker resumes without re-calling the model.
- **Tiger Data:** Postgres with TimescaleDB snapshots, pgvector "related past changes" (open-weight
  `bge-small` embeddings computed in-process), and row-level security on every tenant table.
- **Auth0:** user login, plus **one machine identity per agent** (discovery, repository, impact,
  verification, migration), each with only the scopes its steps need. The Settings → Security page asks
  for a real token and shows the denial when the migration agent tries to push.
- **Backboard:** investigation memory. Follow-up questions ("which file breaks first?") retrieve what was
  found earlier instead of guessing, and the UI shows exactly which memories were used.
- **Browserbase:** real browser sessions for JavaScript-rendered docs, with the session ID kept as evidence.
- **SerpApi:** discovery, clearly labelled "discovery, not proof".
- **Sentry:** agent tracing, with `gen_ai.invoke_agent` → `chat` / `execute_tool` spans carrying real
  token counts. Prompt text is never attached.
- **Entire:** the migration agent's session is recorded as an Entire checkpoint through a small adapter
  for Entire's agent-integration protocol, linked from the investigation page.

**Things that surprised me while building on free plans only:**

- OpenRouter's free Gemma pool was rate-limited upstream, so I moved to Google AI Studio's free tier.
  Gemma 4 there always thinks before answering and rejects a thinking budget, so the adapter strips the
  `<thought>` block before validating.
- Backboard's free credit covers memory and retrieval but not chat. So Backboard stores and retrieves,
  and Gemma writes the answer from exactly what was retrieved. Honestly, I like that split better.
- sentry-sdk 2.73 sends gen_ai spans as a separate span-streaming envelope, not inside
  `transaction.spans`. My first trace assertion found zero spans.
- Entire 0.12 stores checkpoints as git refs (`refs/entire/checkpoints/...`) with 26-character IDs, which
  differs from the docs I started from.

**What isn't done yet (said plainly):**

- GitHub access uses a read-only token, not Auth0 Token Vault. Auth0's async approval (CIBA) isn't on the
  free plan, so approvals are in-app.
- Verification runs only for allow-listed repos, and on free hosting the sandbox can't fully cut network
  access. The environment string on every result says so.
- On free Render, Temporal runs embedded and loses its history when the service sleeps or redeploys.
  Reports and timelines live in Postgres, but an investigation that was mid-flight during a redeploy stays
  stuck at "running".
- The 512 MB free instance can't also hold the ONNX embedding model, which got the container OOM-killed
  once. So "related past changes" (pgvector) runs locally, and is switched off on the hosted demo.

**Related reading on DEV:**

- [Breaking API changes, turned into tested pull requests](https://dev.to/getversyka/breaking-api-changes-turned-into-tested-pull-requests-4o80)
  by getversyka tackles the same pain from the pull-request side. Vendorwake's angle is the evidence
  labels and an open-weight model.
- [How to make an AI research agent label facts vs inferences — a deterministic provenance pipeline](https://dev.to/hexisteme/how-to-make-an-ai-research-agent-label-facts-vs-inferences-a-deterministic-provenance-pipeline-5dfn)
  by hexisteme argues for the same split: the LLM extracts, deterministic code decides what counts as a fact.
- [Connected Is Not Proven: Building an Evidence Graph That Admits What It Doesn't Know](https://dev.to/vivantejani/connected-is-not-proven-building-an-evidence-graph-that-admits-what-it-doesnt-know-4de5)
  by vivantejani is the right instinct for evidence graphs: an edge needs a quote behind it.

## Why Open Innovation Matters

A tool that reads untrusted vendor text and makes claims about my code has to be inspectable end to end:
the model weights, the prompts, the rules that decide "confirmed", and the evidence behind each claim. With
an open-weight model behind a plain adapter, nobody's API decides what my dependency watcher can see, and
anyone can rerun the same investigation and get the same evidence.

## The Lazy Payoff

I stopped reading changelogs. Vendorwake reads them, ignores the ones that don't touch my code, and opens
the ones that do with the file, the line, the failing test and a proposed patch that has already been tested.

For the Stripe upgrade, that work by hand would mean reading a 41-change release, grepping four files,
setting up two virtualenvs, running the tests on both versions, writing patches and testing them again.
Vendorwake did it in about 13 minutes while I wasn't watching, on free tiers. My part was reading one
page and deciding whether to approve.
