// Local mock of the Vendorwake API (docs/api-contract.md) for UI development and checks.
// Test-only: never imported by the app. Node built-ins only.
//
//   node test/mock-api.mjs            # listens on MOCK_PORT (default 8010)
//   MOCK_EMPTY=1 node test/mock-api.mjs   # every list empty, to check empty states
//
// Special ids: any unknown id -> 404; change "c-unavailable" -> 503. Requests without a Bearer
// token -> 401. A connected repository analyses for ~8 s; "Re-verify" runs for ~6 s; inv-2 stays running.

import { createServer } from "node:http";
import { randomUUID } from "node:crypto";

const PORT = Number(process.env.MOCK_PORT || 8010);
const EMPTY = process.env.MOCK_EMPTY === "1";
const T0 = Date.now();
const iso = (offsetMs = 0) => new Date(T0 + offsetMs).toISOString();
const HOUR = 3_600_000;
const SHA = "494e03c6a7b52d4aaa232efe81de25bd56976a02dbe49d9751c99a65db18520a";
const CHANGELOG = "https://raw.githubusercontent.com/stripe/stripe-python/master/CHANGELOG.md";

// ---------------------------------------------------------------------------- state

const repos = EMPTY
  ? []
  : [
      {
        id: "r-1",
        provider: "github",
        owner: "shaileshvats",
        name: "vendorwake",
        ref: "main",
        subpath: "fixtures/stripe-billing-demo",
        html_url: "https://github.com/shaileshvats/vendorwake",
        analysis_status: "completed",
        last_analyzed_at: iso(-2 * HOUR),
        commit_sha: "0a5093a4975f0854892b0ddb5c33a9e0720215be",
        integration_count: 1,
        created_at: iso(-3 * HOUR),
        _readyAt: 0,
      },
    ];

const inventory = {
  repo: { provider: "github", owner: "shaileshvats", name: "vendorwake", ref: "main", subpath: "fixtures/stripe-billing-demo" },
  commit_sha: "0a5093a4975f0854892b0ddb5c33a9e0720215be",
  analyzed_at: iso(-2 * HOUR),
  files_scanned: 14,
  dependencies: [
    { ecosystem: "pypi", package: "stripe", version_spec: "==15.6.1", pinned_version: "15.6.1", manifest_path: "requirements.txt", line: 1 },
    { ecosystem: "pypi", package: "pytest", version_spec: ">=8", pinned_version: null, manifest_path: "requirements.txt", line: 2 },
  ],
  integrations: [
    {
      vendor: "Stripe",
      integration_type: "sdk",
      packages: [{ ecosystem: "pypi", package: "stripe", version_spec: "==15.6.1", pinned_version: "15.6.1", manifest_path: "requirements.txt", line: 1 }],
      detected_version: "15.6.1",
      confidence: 0.98,
      references: [
        { file_path: "billing/refunds.py", line: 14, symbol: "stripe.Reversal", snippet: "reversal = stripe.Reversal.retrieve(transfer_id, reversal_id)" },
        { file_path: "billing/raw.py", line: 9, symbol: "StripeObject.request", snippet: 'return obj.request("post", "/v1/charges", params)' },
        { file_path: "billing/customers.py", line: 22, symbol: "customers.retrieve", snippet: 'client.v1.customers.retrieve(customer="cus_123")' },
        { file_path: "billing/charges.py", line: 3, symbol: "stripe", snippet: "import stripe" },
      ],
    },
  ],
};

const vendors = EMPTY
  ? []
  : [
      {
        name: "Stripe",
        category: "payments",
        website: "https://stripe.com",
        monitoring_status: "active",
        official_domains: ["stripe.com", "docs.stripe.com", "github.com/stripe", "raw.githubusercontent.com/stripe"],
        sources: [
          { url: CHANGELOG, source_type: "changelog", last_checked_at: iso(-HOUR) },
          { url: "https://docs.stripe.com/changelog", source_type: "api_changelog", last_checked_at: iso(-HOUR) },
        ],
      },
      { name: "Twilio", category: "communications", website: "https://www.twilio.com", monitoring_status: "off", official_domains: ["twilio.com"], sources: [] },
    ];

const changeSummaries = EMPTY
  ? []
  : [
      {
        id: "c-1",
        vendor: "Stripe",
        title: "stripe-python 16.0.0: breaking SDK changes",
        change_type: "breaking",
        severity: "high",
        published_at: "2026-09-30T00:00:00Z",
        detected_at: iso(-5 * HOUR),
        source_url: CHANGELOG,
        is_official: true,
        change_count: 3,
      },
      {
        id: "c-2",
        vendor: "Stripe",
        title: "stripe-python 15.0.0: removed legacy list helpers",
        change_type: "removal",
        severity: "medium",
        published_at: "2026-06-12T00:00:00Z",
        detected_at: iso(-60 * 24 * HOUR),
        source_url: CHANGELOG,
        is_official: true,
        change_count: 1,
      },
      {
        id: "c-unavailable",
        vendor: "Stripe",
        title: "Blog: Stripe API rate limit changes (unofficial)",
        change_type: "rate_limit",
        severity: "low",
        published_at: null,
        detected_at: iso(-30 * HOUR),
        source_url: "https://example-blog.dev/stripe-rate-limits",
        is_official: false,
        change_count: 0,
      },
    ];

const changeDetail = {
  ...changeSummaries[0],
  extraction: {
    vendor: "Stripe",
    package: "stripe",
    version: "16.0.0",
    release_date: "2026-09-30",
    pinned_api_version: "2026-09-30.endive",
    requires_human_review: false,
    changes: [
      {
        title: "Rename stripe.Reversal to stripe.TransferReversal",
        change_type: "rename",
        severity: "high",
        affected_symbols: ["stripe.Reversal"],
        replacement: "stripe.TransferReversal",
        evidence_quote: "Rename the `stripe.Reversal` resource class to `stripe.TransferReversal`.",
      },
      {
        title: "Remove deprecated StripeObject.request()",
        change_type: "removal",
        severity: "high",
        affected_symbols: ["StripeObject.request()"],
        replacement: "StripeClient.raw_request()",
        evidence_quote: "The deprecated `StripeObject.request()` method has been removed.",
      },
      {
        title: "Path parameters are positional-only in service methods",
        change_type: "breaking",
        severity: "high",
        affected_symbols: [],
        replacement: null,
        evidence_quote: "Path parameters must now be passed positionally to service methods.",
      },
    ],
  },
  grounding: { ungrounded_quotes: [], ungrounded_symbols: [], ok: true },
  source: { url: CHANGELOG, retrieved_at: "2026-10-24T19:09:15Z", fetched_via: "browserbase", content_sha256: SHA, browser_session_id: "bb_sess_8f2c1e0a" },
  discovery: [
    { url: "https://github.com/stripe/stripe-python/releases/tag/v16.0.0", title: "Release v16.0.0 · stripe/stripe-python", snippet: "Breaking changes: Rename stripe.Reversal…", query: "stripe-python 16.0.0 breaking changes", rank: 1, is_official: true },
    { url: CHANGELOG, title: "CHANGELOG.md", snippet: "16.0.0 - 2026-09-30", query: "stripe-python 16.0.0 breaking changes", rank: 2, is_official: true },
    { url: "https://example-blog.dev/stripe-16-upgrade", title: "Upgrading to stripe-python 16", snippet: "A community write-up of the upgrade.", query: "stripe-python 16.0.0 breaking changes", rank: 3, is_official: false },
  ],
  model_calls: [{ agent: "extraction", provider: "openrouter", model: "google/gemma-4-26b-a4b-it", input_tokens: 2210, output_tokens: 412, latency_s: 3.84, repaired: false }],
  affected_repositories: [{ repository_id: "r-1", investigation_id: "inv-1", impact_status: "confirmed" }],
};

const evidence = [
  { kind: "vendor_source", deterministic: true, description: "Official changelog entry for 16.0.0 (#1915).", source_url: CHANGELOG, content_sha256: SHA, quote: "Rename the `stripe.Reversal` resource class to `stripe.TransferReversal`." },
  { kind: "dependency", deterministic: true, description: "stripe pinned to 15.6.1, inside the affected upgrade path.", file_path: "requirements.txt", line: 1 },
  { kind: "code_reference", deterministic: true, description: "Uses stripe.Reversal.retrieve.", file_path: "billing/refunds.py", line: 14, quote: "reversal = stripe.Reversal.retrieve(transfer_id, reversal_id)" },
  { kind: "code_reference", deterministic: true, description: "Calls StripeObject.request().", file_path: "billing/raw.py", line: 9, quote: 'return obj.request("post", "/v1/charges", params)' },
  { kind: "code_reference", deterministic: true, description: "Passes a path parameter by keyword.", file_path: "billing/customers.py", line: 22, quote: 'client.v1.customers.retrieve(customer="cus_123")' },
  { kind: "test_result", deterministic: true, description: "Sandbox test fails on stripe 16.0.0: TypeError positional-only argument.", file_path: "tests/test_customers.py", line: 8 },
  { kind: "model_inference", deterministic: false, description: "charges.py only imports stripe and uses Charge.create positionally; likely unaffected.", file_path: "billing/charges.py", line: 3 },
  { kind: "search_result", deterministic: false, description: "Community upgrade guide mentions the same three breaks.", source_url: "https://example-blog.dev/stripe-16-upgrade" },
];

const graph = {
  nodes: [
    { id: "src", type: "vendor_source", label: "CHANGELOG 16.0.0" },
    { id: "blog", type: "vendor_source", label: "example-blog.dev" },
    { id: "ch1", type: "change", label: "Reversal → TransferReversal" },
    { id: "ch2", type: "change", label: "StripeObject.request() removed" },
    { id: "ch3", type: "change", label: "Positional-only path params" },
    { id: "s1", type: "symbol", label: "stripe.Reversal" },
    { id: "s2", type: "symbol", label: "StripeObject.request" },
    { id: "s3", type: "symbol", label: "customers.retrieve" },
    { id: "int", type: "integration", label: "stripe-python 15.6.1" },
    { id: "f1", type: "file", label: "billing/refunds.py", status: "confirmed" },
    { id: "f2", type: "file", label: "billing/raw.py", status: "confirmed" },
    { id: "f3", type: "file", label: "billing/customers.py", status: "confirmed" },
    { id: "f4", type: "file", label: "billing/charges.py", status: "unaffected" },
    { id: "t1", type: "test", label: "test_customers.py", status: "confirmed" },
    { id: "risk", type: "risk", label: "Refunds + customer lookup break on upgrade", status: "confirmed" },
  ],
  edges: [
    { source: "src", target: "ch1", label: "announces", evidence_index: 0 },
    { source: "src", target: "ch2", label: "announces", evidence_index: 0 },
    { source: "src", target: "ch3", label: "announces", evidence_index: 0 },
    { source: "blog", target: "ch3", label: "mentions", evidence_index: 7 },
    { source: "ch1", target: "s1", label: "renames" },
    { source: "ch2", target: "s2", label: "removes" },
    { source: "ch3", target: "s3", label: "changes signature" },
    { source: "s1", target: "f1", label: "used in", evidence_index: 2 },
    { source: "s2", target: "f2", label: "used in", evidence_index: 3 },
    { source: "s3", target: "f3", label: "used in", evidence_index: 4 },
    { source: "int", target: "f1", label: "pinned", evidence_index: 1 },
    { source: "int", target: "f4", label: "imported by", evidence_index: 6 },
    { source: "f3", target: "t1", label: "verified by", evidence_index: 5 },
    { source: "f1", target: "risk", label: "leads to" },
    { source: "t1", target: "risk", label: "proves", evidence_index: 5 },
  ],
};

const PATCH = `--- a/billing/refunds.py
+++ b/billing/refunds.py
@@ -11,7 +11,7 @@ import stripe


 def fetch_reversal(transfer_id: str, reversal_id: str):
-    reversal = stripe.Reversal.retrieve(transfer_id, reversal_id)
+    reversal = stripe.TransferReversal.retrieve(transfer_id, reversal_id)
     return reversal


--- a/billing/customers.py
+++ b/billing/customers.py
@@ -20,4 +20,4 @@ def lookup(client):
     # path parameters are positional-only from stripe 16.0.0
-    return client.v1.customers.retrieve(customer="cus_123")
+    return client.v1.customers.retrieve("cus_123")
`;

const recommendations = {
  "inv-1": [
    {
      id: "rec-1",
      recommendation_type: "code_change",
      description: "Rename stripe.Reversal to stripe.TransferReversal and pass the customer id positionally.",
      file_path: "billing/refunds.py",
      patch: PATCH,
      approval_status: "pending",
      verification_status: "passed",
      verification: {
        status: "passed",
        command: "python -m pytest -q tests",
        exit_code: 0,
        stdout_tail: "....                                                     [100%]\n4 passed in 0.41s",
        stderr_tail: "",
        duration_s: 6.2,
        environment: "subprocess venv, python 3.12, stripe==16.0.0, network=off",
        before_version: "15.6.1",
        after_version: "16.0.0",
      },
      _verifyUntil: 0,
    },
    {
      id: "rec-2",
      recommendation_type: "review",
      description: "Replace StripeObject.request() in billing/raw.py with StripeClient.raw_request(); needs a human decision on error handling.",
      file_path: "billing/raw.py",
      patch: null,
      approval_status: "pending",
      verification_status: null,
      verification: null,
      _verifyUntil: 0,
    },
  ],
  "inv-2": [],
};

const assessment = {
  overall_status: "confirmed",
  severity: "high",
  confidence: 0.86,
  summary:
    "stripe-python 16.0.0 breaks three call sites in the billing fixture. stripe.Reversal (refunds.py:14) and StripeObject.request() (raw.py:9) no longer exist, and customers.retrieve(customer=…) (customers.py:22) raises TypeError because path parameters are positional-only. A sandbox test run on 16.0.0 reproduces the TypeError. charges.py is unaffected.",
  affected: [
    { change_title: "Rename stripe.Reversal", file_path: "billing/refunds.py", line: 14, symbol: "stripe.Reversal", status: "confirmed", rationale: "Exact symbol reference; class removed in 16.0.0.", evidence: [evidence[0], evidence[2]] },
    { change_title: "Remove StripeObject.request()", file_path: "billing/raw.py", line: 9, symbol: "StripeObject.request", status: "confirmed", rationale: "Exact method call; method removed.", evidence: [evidence[3]] },
    { change_title: "Positional-only path params", file_path: "billing/customers.py", line: 22, symbol: "customers.retrieve", status: "confirmed", rationale: "Keyword path parameter; sandbox test fails.", evidence: [evidence[4], evidence[5]] },
    { change_title: "Positional-only path params", file_path: "billing/charges.py", line: 3, symbol: "stripe", status: "unaffected", rationale: "No keyword path parameters or removed symbols.", evidence: [evidence[6]] },
  ],
  evidence,
  graph,
  recommendations: recommendations["inv-1"].map((rec) => {
    const copy = { ...rec };
    for (const key of ["id", "approval_status", "verification_status", "_verifyUntil"]) delete copy[key];
    return copy;
  }),
  requires_human_review: false,
  model_calls: [
    { agent: "impact", provider: "openrouter", model: "google/gemma-4-26b-a4b-it", input_tokens: 5120, output_tokens: 880, latency_s: 7.31, repaired: false },
    { agent: "migration", provider: "openrouter", model: "google/gemma-4-26b-a4b-it", input_tokens: 3010, output_tokens: 640, latency_s: 5.02, repaired: true },
  ],
};

const investigations = EMPTY
  ? []
  : [
      {
        id: "inv-1",
        change: { id: "c-1", vendor: "Stripe", title: changeSummaries[0].title, severity: "high" },
        repository: { id: "r-1", owner: "shaileshvats", name: "vendorwake" },
        status: "waiting_approval",
        impact_status: "confirmed",
        confidence: 0.86,
        created_at: iso(-4 * HOUR),
        completed_at: iso(-3.8 * HOUR),
      },
      {
        id: "inv-2",
        change: { id: "c-2", vendor: "Stripe", title: changeSummaries[1].title, severity: "medium" },
        repository: { id: "r-1", owner: "shaileshvats", name: "vendorwake" },
        status: "running",
        impact_status: null,
        confidence: null,
        created_at: iso(-60_000),
        completed_at: null,
      },
    ];

function investigationDetail(summary) {
  const done = summary.id === "inv-1";
  return {
    ...summary,
    summary: done ? assessment.summary : null,
    assessment: done ? assessment : null,
    workflow: { id: `investigate-${summary.id}`, run_id: "7b3c9e2a-1f4d-4c8e-9a51-2d6f0e8b4c11", status: done ? "RUNNING" : "RUNNING", ui_url: `http://localhost:8233/namespaces/default/workflows/investigate-${summary.id}` },
    trace: done ? { sentry_trace_id: "4bf92f3577b34da6a3ce929d0e0e4736", url: "https://example.sentry.io/explore/traces/trace/4bf92f3577b34da6a3ce929d0e0e4736" } : null,
    agent_session: done
      ? {
          provider: "entire",
          session_id: "vw-inv-1-migration",
          checkpoint_id: "01JC8Z4K7M2Q9R5T3V6W8X0Y1Z",
          commit_sha: "9f1c2b7e4d3a5b6c7d8e9f0a1b2c3d4e5f6a7b8c",
          transcript_excerpt: "user: Propose a minimal patch for stripe 16.0.0 breaks in billing/.\nassistant: Renaming stripe.Reversal → stripe.TransferReversal (refunds.py:14) and passing the customer id positionally (customers.py:22).",
          url: null,
        }
      : null,
    timeline: done
      ? [
          { at: iso(-4 * HOUR), step: "fetch_source", status: "completed", detail: "Fetched CHANGELOG.md via Browserbase (sha256 494e03c6…)" },
          { at: iso(-4 * HOUR + 20_000), step: "map_code", status: "completed", detail: "4 reference sites in 4 files" },
          { at: iso(-4 * HOUR + 60_000), step: "assess_impact", status: "completed", detail: "confirmed, 3 affected" },
          { at: iso(-4 * HOUR + 90_000), step: "verify_in_sandbox", status: "completed", detail: "pytest exit 1 on 16.0.0 (TypeError), exit 0 with patch" },
          { at: iso(-3.8 * HOUR), step: "await_approval", status: "running", detail: "Waiting for a human decision" },
        ]
      : [
          { at: iso(-60_000), step: "fetch_source", status: "completed", detail: null },
          { at: iso(-30_000), step: "map_code", status: "running", detail: null },
        ],
  };
}

// ---------------------------------------------------------------------------- helpers

function send(res, status, body) {
  res.writeHead(status, { "content-type": "application/json" });
  res.end(body === undefined ? "" : JSON.stringify(body));
}
const error = (res, status, code, message) => send(res, status, { error: { code, message } });

function readBody(req) {
  return new Promise((resolve) => {
    let raw = "";
    req.on("data", (chunk) => (raw += chunk));
    req.on("end", () => {
      try {
        resolve(raw ? JSON.parse(raw) : {});
      } catch {
        resolve({});
      }
    });
  });
}

function publicRepo(repo) {
  const { _readyAt, ...rest } = repo;
  const now = Date.now();
  if (_readyAt && now < _readyAt) {
    return { ...rest, analysis_status: now < _readyAt - 5_000 ? "pending" : "running" };
  }
  if (_readyAt && rest.analysis_status !== "completed") {
    repo.analysis_status = "completed";
    repo.last_analyzed_at = new Date(_readyAt).toISOString();
    repo.integration_count = 1;
    repo.commit_sha = "0a5093a4975f0854892b0ddb5c33a9e0720215be";
    return publicRepo(repo);
  }
  return rest;
}

function publicRec(rec) {
  const { _verifyUntil, ...rest } = rec;
  if (_verifyUntil && Date.now() < _verifyUntil) return { ...rest, verification_status: "pending" };
  return rest;
}

const AGENTS = [
  { agent: "research-agent", client_id: "AbC1research0000000000000000000", scopes: ["docs:read"] },
  { agent: "analysis-agent", client_id: "AbC2analysis0000000000000000000", scopes: ["repo:read", "docs:read"] },
  { agent: "migration-agent", client_id: "AbC3migration000000000000000000", scopes: ["repo:read", "patch:propose"] },
];
const SCOPE = { read_docs: "docs:read", read_repo: "repo:read", propose_patch: "patch:propose", push_branch: "repo:write" };

// ---------------------------------------------------------------------------- routes

const server = createServer(async (req, res) => {
  const url = new URL(req.url, "http://mock");
  const path = url.pathname;
  const method = req.method;
  console.log(`${method} ${path}${url.search}`);

  if (path === "/api/health") return send(res, 200, { status: "ok", version: "mock", checks: { database: "ok", temporal: "ok" } });
  if (!/^Bearer \S+/.test(req.headers.authorization || "")) return error(res, 401, "unauthorized", "Missing bearer token");
  if (path === "/api/me") return send(res, 200, { subject: "auth0|mock-user", email: "dev@example.com", scopes: [] });

  let m;
  // repositories
  if (path === "/api/repositories" && method === "GET") return send(res, 200, repos.map(publicRepo));
  if (path === "/api/repositories" && method === "POST") {
    const body = await readBody(req);
    if (!body.owner || !body.name) return error(res, 422, "validation_error", "owner and name are required");
    const repo = {
      id: `r-${randomUUID().slice(0, 8)}`,
      provider: "github",
      owner: body.owner,
      name: body.name,
      ref: body.ref || "main",
      subpath: body.subpath || "",
      html_url: `https://github.com/${body.owner}/${body.name}`,
      analysis_status: "pending",
      last_analyzed_at: null,
      commit_sha: null,
      integration_count: 0,
      created_at: new Date().toISOString(),
      _readyAt: Date.now() + 8_000,
    };
    repos.push(repo);
    return send(res, 201, publicRepo(repo));
  }
  if ((m = path.match(/^\/api\/repositories\/([^/]+)$/)) && method === "GET") {
    const repo = repos.find((r) => r.id === m[1]);
    return repo ? send(res, 200, publicRepo(repo)) : error(res, 404, "not_found", "Repository not found");
  }
  if ((m = path.match(/^\/api\/repositories\/([^/]+)\/analyze$/)) && method === "POST") {
    const repo = repos.find((r) => r.id === m[1]);
    if (!repo) return error(res, 404, "not_found", "Repository not found");
    repo.analysis_status = "running";
    repo._readyAt = Date.now() + 8_000;
    return send(res, 202, publicRepo(repo));
  }
  if ((m = path.match(/^\/api\/repositories\/([^/]+)\/dependencies$/))) {
    const repo = repos.find((r) => r.id === m[1]);
    if (!repo) return error(res, 404, "not_found", "Repository not found");
    if (publicRepo(repo).analysis_status !== "completed" && !repo.last_analyzed_at) return error(res, 404, "not_found", "No analysis yet");
    return send(res, 200, { ...inventory, repo: { provider: "github", owner: repo.owner, name: repo.name, ref: repo.ref, subpath: repo.subpath } });
  }

  // vendors & monitoring
  if (path === "/api/vendors") return send(res, 200, vendors);
  if ((path === "/api/monitoring/start" || path === "/api/monitoring/stop") && method === "POST") {
    const body = await readBody(req);
    const vendor = vendors.find((v) => v.name === body.vendor);
    if (!vendor) return error(res, 404, "not_found", "Unknown vendor");
    const start = path.endsWith("start");
    vendor.monitoring_status = start ? "active" : "paused";
    return send(res, 200, start ? { vendor: vendor.name, monitoring_status: "active", schedule_id: `monitor-${vendor.name.toLowerCase()}` } : { vendor: vendor.name, monitoring_status: "paused" });
  }
  if ((m = path.match(/^\/api\/vendors\/([^/]+)\/discover$/)) && method === "POST") {
    const name = decodeURIComponent(m[1]);
    if (!vendors.some((v) => v.name === name)) return error(res, 404, "not_found", "Unknown vendor");
    return send(res, 202, { workflow_id: `discover-${name.toLowerCase()}-${randomUUID().slice(0, 6)}` });
  }

  // changes
  if (path === "/api/changes") {
    const vendor = url.searchParams.get("vendor");
    return send(res, 200, vendor ? changeSummaries.filter((c) => c.vendor === vendor) : changeSummaries);
  }
  if ((m = path.match(/^\/api\/changes\/([^/]+)(\/related|\/investigate)?$/))) {
    const [, id, sub] = m;
    if (id === "c-unavailable") return error(res, 503, "service_unavailable", "Extraction model is not configured");
    const summary = changeSummaries.find((c) => c.id === id);
    if (!summary) return error(res, 404, "not_found", "Change not found");
    if (sub === "/related") return send(res, 200, id === "c-1" ? [{ id: "c-2", vendor: "Stripe", title: changeSummaries[1].title, similarity: 0.71, detected_at: changeSummaries[1].detected_at }] : []);
    if (sub === "/investigate" && method === "POST") {
      const body = await readBody(req);
      const repo = repos.find((r) => r.id === body.repository_id);
      if (!repo) return error(res, 404, "not_found", "Repository not found");
      const inv = {
        id: `inv-${randomUUID().slice(0, 8)}`,
        change: { id: summary.id, vendor: summary.vendor, title: summary.title, severity: summary.severity },
        repository: { id: repo.id, owner: repo.owner, name: repo.name },
        status: "queued",
        impact_status: null,
        confidence: null,
        created_at: new Date().toISOString(),
        completed_at: null,
      };
      investigations.push(inv);
      recommendations[inv.id] = [];
      return send(res, 202, { investigation_id: inv.id, workflow_id: `investigate-${inv.id}` });
    }
    if (id === "c-1") return send(res, 200, changeDetail);
    return send(res, 200, { ...summary, extraction: null, grounding: null, source: null, discovery: [], model_calls: [], affected_repositories: [] });
  }

  // investigations
  if (path === "/api/investigations") return send(res, 200, investigations);
  if ((m = path.match(/^\/api\/investigations\/([^/]+)(\/evidence|\/recommendations|\/ask)?$/))) {
    const [, id, sub] = m;
    const inv = investigations.find((i) => i.id === id);
    if (!inv) return error(res, 404, "not_found", "Investigation not found");
    if (sub === "/evidence") return send(res, 200, id === "inv-1" ? evidence : evidence.slice(0, 2));
    if (sub === "/recommendations") return send(res, 200, (recommendations[id] || []).map(publicRec));
    if (sub === "/ask" && method === "POST") {
      const body = await readBody(req);
      await new Promise((r) => setTimeout(r, 800));
      return send(res, 200, {
        answer: `Mock answer to: "${body.question}". Three call sites break on 16.0.0: billing/refunds.py:14, billing/raw.py:9 and billing/customers.py:22.`,
        retrieved_memories: [
          { memory: "Investigation inv-1: stripe 15.6.1 pinned in requirements.txt:1.", score: 0.83, id: "mem_01" },
          { memory: "Sandbox: pytest exit 1 on 16.0.0 with TypeError in customers.retrieve.", score: 0.77, id: "mem_02" },
        ],
        model: "google/gemma-4-26b-a4b-it",
      });
    }
    return send(res, 200, investigationDetail(inv));
  }
  if ((m = path.match(/^\/api\/recommendations\/([^/]+)\/(approve|dismiss|verify)$/)) && method === "POST") {
    const [, id, action] = m;
    const rec = Object.values(recommendations).flat().find((r) => r.id === id);
    if (!rec) return error(res, 404, "not_found", "Recommendation not found");
    if (action === "approve") rec.approval_status = "approved";
    if (action === "dismiss") rec.approval_status = "dismissed";
    if (action === "verify") rec._verifyUntil = Date.now() + 6_000;
    return send(res, action === "verify" ? 202 : 200, publicRec(rec));
  }

  // security
  if (path === "/api/security/agents") return send(res, 200, EMPTY ? [] : AGENTS);
  if (path === "/api/security/agents/check" && method === "POST") {
    const body = await readBody(req);
    const agent = AGENTS.find((a) => a.agent === body.agent);
    const required = SCOPE[body.operation];
    if (!agent || !required) return error(res, 422, "validation_error", "Unknown agent or operation");
    const allowed = agent.scopes.includes(required);
    return send(res, 200, {
      agent: agent.agent,
      operation: body.operation,
      required_scope: required,
      allowed,
      reason: allowed
        ? `Token for ${agent.agent} includes ${required}.`
        : `Token for ${agent.agent} lacks ${required} (granted: ${agent.scopes.join(" ")}). Denial logged.`,
    });
  }

  return error(res, 404, "not_found", `No mock route for ${method} ${path}`);
});

server.listen(PORT, "0.0.0.0", () => console.log(`mock API on :${PORT}${EMPTY ? " (empty)" : ""}`));
