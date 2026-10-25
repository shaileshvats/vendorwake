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

> `payments/refunds.py:4` uses `stripe.Reversal`, which 16.0.0 renamed to `stripe.TransferReversal`.
> **Confirmed:** the fixture's test passes on 15.6.1 and fails on 16.0.0 with `AttributeError`. Here is the
> one-line patch, and the test passes again with it applied.

Every finding carries one of three labels, and the label means something:

- **Confirmed**: deterministic proof. An exact symbol reference in my code *and* a removed or renamed API,
  or a test that fails under the new version.
- **Potential**: a plausible connection the model found, waiting for proof.
- **Unaffected**: the vendor or symbol isn't used.

The model can explain and downgrade a finding, but **it cannot upgrade one**. That rule is enforced in code,
not in the prompt.

## Demo

- **Live app:** <!-- FILL: https://vendorwake-web.onrender.com -->
- **API docs:** <!-- FILL: https://vendorwake-api.onrender.com/api/docs -->
- **Walkthrough:** <!-- FILL: video link or screenshots of the investigation page -->

The demo investigation is a real upstream change: **stripe-python 16.0.0** (released 2026-09-30) against a
small billing fixture pinned to 15.6.1. All three breaking changes reproduce offline:

| Changelog item | 15.6.1 | 16.0.0 |
|---|---|---|
| `stripe.Reversal` → `stripe.TransferReversal` | exists | `AttributeError` |
| `StripeObject.request()` removed | exists | `AttributeError` |
| Path params positional-only | keyword accepted | `TypeError: ... positional-only arguments passed as keyword` |

<!-- FILL: real numbers from the hosted run: files scanned, findings by status, tokens, latency -->

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
- On free Render, Temporal runs embedded and loses its history when the service sleeps. Reports and
  timelines live in Postgres, so nothing user-visible is lost.

<!-- My Agent Session: embed the DevRelay-saved session here with the agent_session tag, or link it. -->

## Why Open Innovation Matters

A tool that reads untrusted vendor text and makes claims about my code has to be inspectable end to end:
the model weights, the prompts, the rules that decide "confirmed", and the evidence behind each claim. With
an open-weight model behind a plain adapter, nobody's API decides what my dependency watcher can see, and
anyone can rerun the same investigation and get the same evidence.

## The Lazy Payoff

I stopped reading changelogs. Vendorwake reads them, ignores the ones that don't touch my code, and opens
the ones that do with the file, the line, the failing test and the patch already attached.
