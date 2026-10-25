# API contract (Week 3 build)

All routes are under `/api`. Unless marked *public*, they need `Authorization: Bearer <Auth0 user access
token>`; data is scoped to the caller by row-level security. Errors use
`{"error": {"code": str, "message": str, "details"?: [...]}}`. Times are ISO-8601 UTC.
Pydantic shapes named below live in `backend/vendorwake/domain/contracts.py` and are serialised as-is.

## Health and identity
| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/health` *public* | – | `{status, version, checks: {database, temporal}}` |
| GET | `/me` | – | `{subject, email, scopes}` |

## Repositories
| Method | Path | Body | Response |
|---|---|---|---|
| POST | `/repositories` | `{owner, name, ref?="main", subpath?=""}` | `201 Repository` (analysis starts immediately) |
| GET | `/repositories` | – | `Repository[]` |
| GET | `/repositories/{id}` | – | `Repository` |
| POST | `/repositories/{id}/analyze` | – | `202 Repository` |
| GET | `/repositories/{id}/dependencies` | – | `RepositoryInventory` (404 until first analysis finishes) |

`Repository = {id, provider, owner, name, ref, subpath, html_url, analysis_status: "pending"|"running"|"completed"|"failed", last_analyzed_at, commit_sha, integration_count, created_at}`

## Vendors, monitoring and changes
| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/vendors` | – | `Vendor[]` |
| POST | `/monitoring/start` | `{vendor}` | `{vendor, monitoring_status: "active", schedule_id}` |
| POST | `/monitoring/stop` | `{vendor}` | `{vendor, monitoring_status: "paused"}` |
| POST | `/vendors/{vendor}/discover` | – | `202 {workflow_id}` (runs discovery now) |
| GET | `/changes` | query `vendor?` | `ChangeSummary[]` |
| GET | `/changes/{id}` | – | `ChangeDetail` |
| GET | `/changes/{id}/related` | – | `RelatedChange[]` (pgvector similarity over past changes) |
| POST | `/changes/{id}/investigate` | `{repository_id}` | `202 {investigation_id, workflow_id}` |

- `Vendor = {name, category, website, monitoring_status: "active"|"paused"|"off", official_domains, sources: [{url, source_type, last_checked_at}]}`
- `ChangeSummary = {id, vendor, title, change_type, severity, published_at, detected_at, source_url, is_official, change_count}`
- `ChangeDetail = ChangeSummary + {extraction: ChangeExtraction, grounding: GroundingReport + {ok}, source: {url, retrieved_at, fetched_via, content_sha256, browser_session_id}, discovery: DiscoveryCandidate[], model_calls: ModelCallInfo[], affected_repositories: [{repository_id, investigation_id, impact_status}]}`
- `RelatedChange = {id, vendor, title, similarity, detected_at}`

## Investigations
| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/investigations` | – | `InvestigationSummary[]` |
| GET | `/investigations/{id}` | – | `InvestigationDetail` |
| GET | `/investigations/{id}/evidence` | – | `EvidenceItem[]` |
| GET | `/investigations/{id}/recommendations` | – | `RecommendationOut[]` |
| POST | `/investigations/{id}/ask` | `{question}` | `{answer, retrieved_memories: [{memory, score, id}], model}` |
| POST | `/recommendations/{id}/approve` | – | `RecommendationOut` |
| POST | `/recommendations/{id}/dismiss` | – | `RecommendationOut` |
| POST | `/recommendations/{id}/verify` | – | `202 RecommendationOut` |

- `InvestigationSummary = {id, change: {id, vendor, title, severity}, repository: {id, owner, name}, status: "queued"|"running"|"waiting_approval"|"completed"|"failed", impact_status, confidence, created_at, completed_at}`
- `InvestigationDetail = InvestigationSummary + {summary, assessment: ImpactAssessment | null, workflow: {id, run_id, status, ui_url}, trace: {sentry_trace_id, url} | null, agent_session: {provider: "entire", session_id, checkpoint_id, commit_sha, transcript_excerpt, url} | null, timeline: [{at, step, status, detail}]}`
- `RecommendationOut = Recommendation + {id, approval_status: "pending"|"approved"|"dismissed", verification_status}`

## Agent permissions (Auth0 demo)
| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/security/agents` | – | `[{agent, client_id, scopes}]` |
| POST | `/security/agents/check` | `{agent, operation}` | `{agent, operation, required_scope, allowed, reason}` (performs the real token request + scope check; denials are logged) |

Operations: `read_docs` (`docs:read`), `read_repo` (`repo:read`), `propose_patch` (`patch:propose`), `push_branch` (`repo:write`; no agent holds it, and pushing additionally requires human approval).
