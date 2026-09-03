# Studio Production Commander

**End-to-End Production Architecture Specification**

Agentic Cinema: The Blockbuster Hackathon | Grafana Labs Track
Target: 50+ concurrent users | Submission deadline 9 September 2026, 14:00 PT

---

## 0. Implementation status

**This document is the target design, not a description of the running system.**
Where the two differ, the code is authoritative. The gaps that matter:

| Area | Specified here | As built |
|---|---|---|
| Run state, events, evidence | Firestore | In-process store in `agent-worker` |
| Approval and audit records | Firestore | In-process store in `action-executor` |
| Tenant leases | Memorystore Redis | In-process store in `api-gateway` |
| Run dispatch | Pub/Sub | Direct HTTP call from `api-gateway` |
| Event delivery to browser | Firestore snapshots | `stream-service` polls `agent-worker` over HTTP |
| MCP response cache | Memorystore Redis | Redis when reachable, in-process cache otherwise |
| Production metadata | Cloud SQL Postgres | In-memory SQLite by default; Postgres via `DATABASE_URL` |
| Trace search (section 6.2) | Tempo via MCP | Implemented via `tempo_traceql-search`; `render-sim` emits the frame spans it reads |

Consequently the system runs as a **single instance**. It starts with no managed
backend at all, which is why it is easy to run and to deploy, but it does not
scale horizontally as written. `infra/terraform/` provisions the managed
backends for the scale-out path; those manifests have not been applied and are
not wired into the code.

Sections 5 and 14 describe capacity for the distributed design and should be read
against that caveat.

---

## 1. Purpose and Scope

This document defines the runtime architecture, data model, concurrency design, and operational limits for Studio Production Commander. It supersedes all earlier architecture sections in the project specification.

The system is a Gemini agent using the Grafana Cloud MCP server to investigate render-pipeline incidents, translate them into VFX delivery impact, propose ranked remediations, and verify recovery after a human approves an action.

The load target is 50 or more simultaneous users, each able to run an independent investigation against an isolated production world, without exhausting Grafana Cloud quota, Gemini quota, or the project budget.

---

## 2. Design Constraints

- The agent reasoning layer is ours. Grafana supplies observability primitives through MCP and nothing else.
- Grafana MCP must be called at runtime and the connection must be visible in code, not only in the README.
- AI tooling is restricted to Google Cloud AI services and built-in partner AI features. No third-party model providers or agent frameworks.
- Impact arithmetic runs in deterministic code. The model never computes projected delay, shot counts, or recovery times.
- Every write action is gated behind explicit human approval and recorded in an append-only audit log.
- The system must degrade to a read-only cached mode rather than fail when Grafana Cloud rate limits.
- Cold start latency must stay under two seconds for the first agent token, so judges and demo viewers do not see a stall.

---

## 3. System Context

```
   Studio user (production supervisor, VFX coordinator, render wrangler)
                                |
                                v
                 +------------------------------+
                 |  Studio Production Commander |
                 +------------------------------+
                     |            |           |
        reads        |            |           |   writes (approved only)
        telemetry    |            |           |
                     v            v           v
             Grafana Cloud   Production   Render Control
             (Mimir/Loki/     Metadata      Plane API
              Tempo)          Store
                     ^                          |
                     |    OpenTelemetry         |
                     +---- Render Pipeline <----+
```

Grafana Cloud is the observability substrate. The production metadata store holds scenes, shots, frame ranges, priorities, and delivery deadlines. The render control plane is the only surface the agent writes to, and only after approval.

---

## 4. Runtime Architecture

```
                          Browser (React SPA)
                                 |
                        HTTPS + SSE, JWT bearer
                                 |
                                 v
              +---------------------------------------+
              |  Global External Load Balancer        |
              |  Cloud Armor  |  Cloud CDN (static)   |
              +---------------------------------------+
                                 |
        +------------------------+------------------------+
        |                        |                        |
        v                        v                        v
 +--------------+       +----------------+       +----------------+
 | api-gateway  |       | stream-service |       | web (static)   |
 | Cloud Run    |       | Cloud Run      |       | Cloud Run/CDN  |
 | stateless    |       | SSE fan-out    |       |                |
 +--------------+       +----------------+       +----------------+
        |                        ^
        | enqueue run            | run events
        v                        |
   +---------+            +--------------+
   | Pub/Sub |----------->| Firestore    |<---------+
   | runs    |            | run state    |          |
   +---------+            +--------------+          |
        |                                           |
        v                                           |
 +-------------------------------------------+      |
 | agent-worker  (Cloud Run, ADK + Gemini)   |------+
 |  investigation loop, hypothesis testing   |
 +-------------------------------------------+
     |            |               |            |
     v            v               v            v
+----------+ +----------+ +-------------+ +------------+
| mcp-     | | impact-  | | action-     | | Memorystore|
| gateway  | | engine   | | executor    | | Redis      |
| Cloud Run| | Cloud Run| | Cloud Run   | | cache+locks|
+----------+ +----------+ +-------------+ +------------+
     |            |               |
     v            v               v
+-----------+ +-----------+ +---------------+
| Grafana   | | Postgres  | | Render Control|
| Cloud MCP | | (Cloud    | | Plane API     |
| server    | |  SQL)     | | (simulator)   |
+-----------+ +-----------+ +---------------+
     |                              |
     v                              |
 Mimir / Loki / Tempo <-- OTel -----+
```

### 4.1 Service Responsibilities

| Service | Runtime | Responsibility | Scaling |
|---|---|---|---|
| `web` | Cloud Run + CDN | React SPA. Production board, incident timeline, evidence ledger, approval modal. | CDN cached, 1 to 3 instances |
| `api-gateway` | Cloud Run | Auth, request validation, tenant leasing, run creation, approval intake. Stateless. | 2 to 20 instances, 80 concurrency |
| `stream-service` | Cloud Run | Server-sent events. Fans run events out to subscribed browsers. | 2 to 10 instances, 250 concurrency |
| `agent-worker` | Cloud Run | Pulls runs from Pub/Sub. Executes the ADK investigation loop against Gemini. | 3 to 25 instances, concurrency 4 |
| `mcp-gateway` | Cloud Run | Single choke point for all Grafana MCP traffic. Cache, dedupe, rate limit, allowlist. | 2 to 8 instances, concurrency 60 |
| `impact-engine` | Cloud Run | Deterministic production impact and delivery projection. Pure functions, no model. | 2 to 10 instances |
| `action-executor` | Cloud Run | Executes approved remediations with idempotency keys. Writes audit records. | 1 to 4 instances |
| `render-sim` | Cloud Run (min 1) | Multi-tenant render pipeline simulator. Emits OTel metrics, logs, traces. | always warm, 1 to 2 instances |

Every service is stateless except `render-sim`, which holds world state in memory and mirrors it to Firestore for crash recovery. Shared state lives in Firestore, Cloud SQL, and Memorystore.

---

## 5. Concurrency Design

Fifty concurrent users is not a web-serving problem. It is a fan-out problem against two rate-limited upstreams: the Grafana Cloud MCP server and the Gemini API. The architecture solves it with four mechanisms.

### 5.1 Asynchronous Run Model

An investigation takes 30 to 90 seconds and 10 to 14 tool calls. Holding an HTTP request open for that duration wastes an instance slot per user and breaks on load balancer timeouts.

1. Client POSTs `/runs` with an objective and a tenant lease token. The gateway validates, writes a run document to Firestore in state `QUEUED`, publishes to Pub/Sub, and returns a `run_id` in under 200 ms.
2. Client opens `GET /runs/{id}/events` as an SSE stream against `stream-service`.
3. `agent-worker` pulls the message, transitions the run to `RUNNING`, and appends step events to the run document as it works.
4. `stream-service` holds a Firestore listener per run and pushes each appended event to the browser.
5. On disconnect the client re-reads the run document and resumes the stream from the last event index. No work is lost.

This decouples browser connections from agent compute. Fifty streams cost two `stream-service` instances. Fifty investigations cost roughly thirteen `agent-worker` instances at concurrency four.

### 5.2 MCP Gateway: The Critical Component

No service calls Grafana MCP directly. All traffic passes through `mcp-gateway`. This is the highest-leverage piece of the architecture.

```
   50 concurrent agent runs
   ~12 tool calls each = ~600 calls per investigation wave
                    |
                    v
     +--------------------------------------+
     |            mcp-gateway               |
     |                                      |
     |  1. normalize   quantize time range  |
     |                 to 15s buckets       |
     |  2. cache key   sha256(tool, params, |
     |                 tenant, bucket)      |
     |  3. Redis GET   hit  -> return       |
     |  4. singleflight    one in-flight    |
     |                     call per key     |
     |  5. token bucket    global QPS cap   |
     |  6. call MCP        streamable HTTP  |
     |  7. Redis SETEX     TTL 20s          |
     +--------------------------------------+
                    |
                    v
            Grafana Cloud MCP
            ~30 to 60 real calls per wave
            (90 percent+ dedupe)
```

- **Time quantization.** Query windows snap to 15-second buckets. Two users asking about the same incident 4 seconds apart produce an identical cache key rather than two distinct queries.
- **Content-addressed cache.** Key is `sha256` of tool name, normalized parameters, `tenant_id`, and time bucket. Redis TTL of 20 seconds for range queries, 300 seconds for metadata queries such as label and metric name discovery.
- **Singleflight.** Concurrent identical keys collapse into one upstream call. The rest wait on the same promise. This alone removes most of the thundering-herd risk during a scripted demo where everyone triggers the same incident.
- **Global token bucket in Redis.** A hard ceiling on outbound MCP QPS, independent of instance count. Excess requests queue with a 5-second budget, then return a degraded cached result rather than an error.
- **Tool allowlist enforcement.** The gateway rejects any tool name outside the configured allowlist. Compliance is enforced in code, not in a prompt.
- **Structured call log.** Every call records tool, parameters, latency, cache status, and `run_id`. This log is the compliance artifact and the on-screen demo element.

Metadata queries such as metric name discovery are near-static and cache at above 99 percent. Range queries against a shared incident window cache at 85 to 95 percent. Expect 600 logical calls per wave to resolve into 30 to 60 real upstream calls.

### 5.3 Gemini Concurrency Budget

- Per-run step ceiling of 14 tool-selection turns. A runaway loop cannot consume unbounded quota.
- Per-run token budget. Evidence is summarized into a compact ledger rather than replayed as raw tool output on every turn. Raw payloads stay in Firestore and are referenced by id.
- Global semaphore in Redis capping in-flight Gemini calls. Overflow runs sit in Pub/Sub rather than failing.
- Per-user rate limit of three concurrent runs and twenty runs per hour.
- Flash for the planning and tool-selection turns, Pro for the final hypothesis and narrative synthesis. Most turns are cheap; only the reasoning turn is expensive.

### 5.4 Multi-Tenant World Isolation

Fifty users sharing one simulated render farm creates a correctness problem. If one user rolls back the renderer configuration, everyone else sees recovery they did not cause, and the demo becomes incoherent.

```
  One simulator process, N logical productions.

  Every series carries: tenant_id="t07"

  t01 ---+
  t02 ---+---> render-sim ---OTel---> Grafana Cloud
  ...    |      (one process,          Mimir / Loki / Tempo
  t24 ---+       N worlds)

  Session -> tenant lease (Redis, TTL 20 min)
     - user gets exclusive write access to one tenant
     - PromQL and LogQL always filtered by tenant_id
     - a rollback in t07 never touches t08
     - lease expiry resets the world to baseline
```

1. `render-sim` maintains N independent production worlds in one process, sized to the cardinality budget.
2. Every emitted metric, log line, and span carries a `tenant_id` label.
3. On session start, `api-gateway` leases a free tenant from a Redis pool with a 20-minute TTL and a heartbeat.
4. `mcp-gateway` injects a `tenant_id` matcher into every PromQL and LogQL query. A user cannot read or write another world, and cannot remove the matcher through prompt injection because injection happens after the model produces the query.
5. On lease expiry or release, the world resets to baseline and returns to the pool.
6. When the pool is exhausted, new sessions attach in observer mode to a shared read-only world. They see a live incident and can run investigations, but cannot execute remediations.

Observer mode is the pressure valve. It keeps the system usable past the tenant ceiling instead of turning users away.

### 5.5 Cardinality Budget

Metric cardinality is the real ceiling on tenant count, not compute. Series count is the product of tenants, workers per tenant, and metrics per worker.

| Parameter | Value | Note |
|---|---|---|
| Metrics per worker | 13 | duration, queue depth, GPU util, GPU memory, temp, CPU, memory, active jobs, capacity, errors, started, completed, failed |
| Workers per tenant | 8 | enough to show a partial-fleet failure pattern |
| Series per tenant | 104 | 13 x 8 |
| Tenant pool size | 24 | writable worlds |
| Writable series | 2,496 | 104 x 24 |
| Shared observer world | 104 | read-only overflow |
| Pipeline and API series | ~400 | scheduler, queue, asset service, storage |
| **Total active series** | **~3,000** | inside a 10k free-tier allowance with headroom |

Raise the tenant pool by lowering workers per tenant. Twenty-four writable worlds plus observer mode covers 50 concurrent users comfortably, since not every user holds a lease at the same moment.

---

## 6. Agent Design

```
  objective ("will Shadow Protocol miss the 18:00 VFX delivery?")
        |
        v
  +-----------------+
  | PLAN            |  Gemini proposes next evidence to gather
  +-----------------+
        |
        v
  +-----------------+
  | SELECT TOOL     |  constrained to the MCP allowlist
  +-----------------+
        |
        v
  +-----------------+
  | CALL            |  mcp-gateway (cached, quantized, rate limited)
  +-----------------+
        |
        v
  +-----------------+
  | RECORD EVIDENCE |  append-only evidence ledger in Firestore
  +-----------------+
        |
        v
  +-----------------+     no
  | ENOUGH?         |----------> back to PLAN   (max 14 steps)
  +-----------------+
        | yes
        v
  +-----------------+
  | HYPOTHESIS      |  form + score against falsifiable tests
  +-----------------+
        |
        v
  +-----------------+
  | IMPACT (code)   |  deterministic, no model arithmetic
  +-----------------+
        |
        v
  +-----------------+
  | OPTIONS         |  ranked remediation with risk + ETA
  +-----------------+
        |
        v
  +-----------------+
  | HUMAN APPROVAL  |  hard gate, nothing writes without it
  +-----------------+
        |
        v
  +-----------------+
  | EXECUTE         |  idempotency key, audit record
  +-----------------+
        |
        v
  +-----------------+
  | VERIFY          |  re-query Grafana after settle window
  +-----------------+
```

### 6.1 Tool Allowlist

The agent receives an explicit tool list, never the full MCP catalog. The gateway enforces the same list server-side.

```python
MCP_ALLOWLIST = [
    "list_prometheus_metric_names",
    "list_prometheus_label_names",
    "list_prometheus_label_values",
    "query_prometheus",
    "query_prometheus_histogram",
    "list_loki_label_names",
    "list_loki_label_values",
    "query_loki_logs",
    "query_loki_stats",
    "search_tempo_traces",
    "list_alert_rules",
    "get_alert_rule_by_uid",
    "list_incidents",
]
```

Confirm exact tool names against the Grafana MCP tools reference before implementation. Some categories are disabled by default and must be enabled explicitly on the server.

Assistant-native operations (`ask_assistant`, `create_investigation`, and any equivalent) are deliberately excluded. The investigation logic is ours.

### 6.2 Hypothesis Testing

The agent does not accept the first correlation. Each hypothesis is scored against falsifiable tests, and the score becomes the stated confidence.

| Test | Question | Evidence source |
|---|---|---|
| Temporal precedence | Did the suspected change occur before degradation began? | Loki config-load log line vs metric inflection |
| Metric correlation | Did render duration rise immediately after? | Prometheus range query |
| Mechanism | Did GPU utilization fall while duration rose? | Prometheus, rules out simple saturation |
| Localization | Are only workers on the new version affected? | Prometheus by `renderer_version` label |
| Trace attribution | Does render time dominate the span, not storage or API? | Tempo trace query |
| Control group | Did workers without the change stay healthy? | Prometheus negative matcher |

Confidence maps directly: six of six is high, four or five is medium, three or fewer is low and the agent states what evidence would resolve it. A stated confidence backed by named tests reads as engineering. A number produced by a model reads as decoration.

### 6.3 Prompt Injection Defense

- Log lines returned from Loki are untrusted input. They are wrapped in a delimited data block and the system prompt states that content inside carries no instructions.
- Tenant matchers are injected server-side after generation, so no log content can widen query scope.
- The action executor accepts only an enum of known action types with typed parameters. It never accepts free text from the model.
- Any tool call outside the allowlist terminates the run and raises an alert.

---

## 7. Deterministic Impact Engine

The impact engine is a pure service with no model dependency. Given a set of affected workers and a time window, it returns the production consequence. Same inputs always yield the same output, which is what makes the demo defensible under questioning.

```http
POST /impact/project
{
  "tenant_id": "t07",
  "affected_workers": ["w-03","w-07","w-11","w-17"],
  "observed_throughput_fpm": 41.2,
  "baseline_throughput_fpm": 118.6,
  "queue_depth": 18432,
  "as_of": "2026-09-04T14:55:00Z"
}

200 OK
{
  "affected_shots": 1842,
  "high_priority_shots": 217,
  "sequences": ["Final Chase", "Rooftop Pursuit"],
  "deadline": "2026-09-04T18:00:00Z",
  "projected_completion": "2026-09-04T18:47:00Z",
  "delay_minutes": 47,
  "at_risk_deliverables": ["SP_VFX_R04"],
  "method": "queue_depth / observed_throughput_fpm, frames-weighted"
}
```

- The `method` field is returned to the UI. Judges see how the number was derived.
- Shot and deadline data come from Cloud SQL, joined on worker to scene to shot.
- Throughput is a 5-minute trailing rate to avoid whipsawing on a single slow frame.
- Every projection is stored with the run so the before and after comparison is exact.

---

## 8. Approval and Action Execution

1. The agent emits ranked options. Each carries `action_type`, typed parameters, estimated recovery minutes, risk level, and production consequence.
2. The run halts in state `AWAITING_APPROVAL`. No timer auto-approves.
3. The user approves one option. The gateway validates the user holds the tenant lease and generates an idempotency key of `run_id` plus `option_id`.
4. `action-executor` checks the key against Firestore. A duplicate returns the original result rather than acting twice.
5. The executor calls the render control plane with a typed request and writes an audit record: who, what, when, which run, which evidence.
6. The run enters `VERIFYING`. After a 90-second settle window the agent re-queries Grafana and compares against the pre-action baseline.
7. The impact engine recomputes the projection. The UI shows the delivery time moving.

Action types are a closed set: `rollback_renderer_config`, `scale_render_workers`, `reprioritize_queue`, `drain_worker`. Adding an action requires a code change and a schema change, never a prompt change.

---

## 9. Data Model

### 9.1 Firestore

| Collection | Key | Contents |
|---|---|---|
| `runs` | `run_id` | objective, tenant_id, user_id, state, created_at, confidence, hypothesis, options, chosen_option |
| `runs/{id}/events` | `seq` | append-only step stream: plan, tool_call, evidence, hypothesis, impact, approval, verification |
| `runs/{id}/evidence` | `evidence_id` | raw tool payload, tool name, parameters, latency, cache_hit |
| `approvals` | `idempotency_key` | run_id, option_id, user_id, approved_at, executor_result |
| `audit` | auto | immutable action record, retained beyond run lifetime |
| `tenant_leases` | `tenant_id` | session_id, leased_at, heartbeat_at, state |

### 9.2 Cloud SQL (Postgres) production metadata

```sql
productions(production_id, title, studio, status)
sequences(sequence_id, production_id, name)
scenes(scene_id, sequence_id, code)
shots(shot_id, scene_id, code, frame_start, frame_end, priority, status)
deliverables(deliverable_id, production_id, name, deadline_utc)
shot_deliverable(shot_id, deliverable_id)
render_jobs(job_id, shot_id, worker_id, frame, state, started_at, finished_at)
```

The join from a failing worker to an at-risk deliverable runs through `render_jobs` to `shots` to `shot_deliverable`. This is one indexed SQL query, not model reasoning.

---

## 10. Telemetry Simulator

The simulator is a real component, not a fixture file. Judges distinguish a verified control loop from a scripted animation, and the difference decides the technological implementation score.

- Each world runs a discrete-event render farm: job arrival, scheduling, per-worker GPU throughput, completion, failure.
- Renderer configuration is real state. Setting `tile_size` to the regressed value cuts effective GPU throughput, which raises duration, which grows the queue. The causal chain is produced, not asserted.
- A control plane endpoint applies `rollback_renderer_config` and the other three action types. `action-executor` calls it for real.
- OTel export: metrics to Mimir, structured logs to Loki, spans to Tempo, all labelled with `tenant_id`, `worker_id`, `renderer_version`, `gpu_type`, `region`.
- A scenario endpoint injects the regression on demand so the demo starts from a healthy baseline.
- World state mirrors to Firestore every 10 seconds so a restart resumes rather than resetting every tenant.

### 10.1 Grounding the numbers

Synthetic telemetry is unavoidable; ungrounded telemetry is a choice. Two public sources make the numbers defensible:

- **Blender Open Data** (opendata.blender.org). Real GPU render benchmark results, downloadable as JSON and CSV, released as public domain. Use it to set per-device throughput so `gpu_type` differences are real rather than invented.
- **Blender open movies.** Real runtimes convert to real frame counts at 24 fps (Sintel 14m48s, Cosmos Laundromat 12m, Spring 8m), which gives a defensible shot list and frame ranges. Structure is free; complete production files for modern projects require a Blender Studio subscription.

Note: Prometheus and Mimir reject samples timestamped too far in the past, and the out-of-order ingestion window is limited. The demo incident must happen live, which is what the scenario trigger endpoint is for. Backfilling a historical incident from a CSV does not work.

---

## 11. Observability of the Agent Itself

The agent is instrumented with OpenTelemetry and exports to Grafana Cloud. This gives a second, honest use of the partner product and produces a dashboard worth showing in the video.

`services/common/tracing.py` installs a global `TracerProvider` with an OTLP span
exporter. ADK emits GenAI-semantic-convention spans through the global provider,
so agent invocations, LLM calls and tool calls are exported without changing any
ADK call site. `agent-worker` adds a root `investigation` span per run and one
span per MCP call on top of that.

**Implemented:**
- Per-run trace spanning the investigation, each LLM turn, and each tool call.
- Gemini call latency and token counts, from ADK's own span attributes.
- Per-MCP-call spans carrying tool name, latency, cache hit and staleness.

**Not yet implemented:**
- Cost per run as a derived figure, and upstream QPS against the token bucket ceiling.
- Run outcome distribution: resolved, low confidence, abandoned, failed.
- Active tenant leases and observer-mode fallback rate.

A dashboard showing cache hit ratio holding above 90 percent while 50 investigations run is direct evidence the concurrency design works.

---

## 12. Security

- Identity Platform issues JWTs. Anonymous sign-in is acceptable for the demo, with the uid binding the tenant lease.
- The Grafana service account token holds read scopes for datasource query, alerting read, and incident read, plus annotation and incident **write** scopes used solely by the post-approval write-back. Widening the token does not widen what the model can do: write tools are reachable only through `mcp-gateway`'s `/write` endpoint, which requires an approval id, while the agent's query path validates against an allowlist containing no write tool at all. This is asserted per tool in `services/mcp-gateway/tests/test_gateway.py`.
- Secrets live in Secret Manager and mount as environment references, never in images or repository files.
- Internal services accept only ID-token authenticated calls from named service accounts. Ingress is internal-and-load-balancer for everything except `web`, `api-gateway`, and `stream-service`.
- Cloud Armor applies per-IP rate limits and a WAF ruleset at the edge.
- The repository is public. A pre-commit secret scan and a seeded `.env.example` prevent credential leakage, since the license and public repo are hard submission requirements.

---

## 13. Failure Modes and Degraded Operation

| Failure | Detection | Response |
|---|---|---|
| Grafana MCP rate limited or 5xx | gateway error rate, 429 count | Serve stale cache up to 120s, mark evidence as stale in the UI, continue the run |
| Grafana MCP fully unavailable | circuit breaker open | Run halts in `DEGRADED` with partial evidence and a plain explanation. No fabricated findings |
| Gemini quota exhausted | 429 from the API | Exponential backoff, run stays queued in Pub/Sub, user sees a queue position |
| `agent-worker` crash mid-run | Pub/Sub redelivery | Run resumes from the last recorded evidence index. Steps are idempotent |
| Tenant pool exhausted | lease request fails | Session enters observer mode against the shared read-only world |
| `render-sim` restart | health check fail | Worlds restore from the Firestore mirror, at most 10 seconds of drift |
| Action executor timeout | no response in 15s | Idempotency key prevents a double apply. Executor polls control plane for the true state |
| Duplicate approval click | idempotency key hit | Original result returned. No second action |

The rule across all of them: the agent states what it does not know rather than filling a gap. An agent admitting missing evidence is more credible than one that always answers.

---

## 14. Capacity Targets

| Metric | Target | Basis |
|---|---|---|
| Concurrent users | 50+ | 24 writable tenants plus unbounded observer sessions |
| Concurrent investigations | 50 | 13 `agent-worker` instances at concurrency 4 |
| Time to first streamed token | < 2 s | min-instances 1 on `api-gateway` and `agent-worker` |
| Full investigation p50 | 35 s | 10 to 14 tool calls, most served from cache |
| Full investigation p95 | 90 s | cold cache, full 14-step path |
| Upstream MCP QPS | < 25 | global token bucket ceiling |
| MCP cache hit ratio | > 85 % | quantization plus singleflight on a shared incident window |
| Active Prometheus series | ~3,000 | cardinality budget in section 5.5 |
| Run state durability | 100 % | Firestore write before Pub/Sub publish |

These are engineering estimates, not measurements. Replace them with real figures after the load test.

---

## 15. Repository Layout

```
studio-production-commander/
  LICENSE                     Apache-2.0, detectable in About
  README.md                   architecture, setup, demo script
  AGENTS.md                   agent context for Antigravity
  .agents/rules/              workspace rules
  .agents/workflows/          slash-command workflows
  infra/terraform/            Cloud Run, Pub/Sub, Firestore, SQL, Redis, LB
  services/
    api-gateway/              FastAPI, auth, leases, run creation
    stream-service/           SSE fan-out
    agent-worker/             google-adk agent, investigation loop
      agent/planner.py
      agent/hypothesis.py
      agent/tools.py          MCP allowlist binding
    mcp-gateway/              cache, singleflight, token bucket, allowlist
    impact-engine/            deterministic projection, fully unit tested
    action-executor/          idempotent approved actions, audit
    render-sim/               multi-tenant simulator, OTel export
  web/                        React SPA
  docs/                       this specification, diagrams
  scripts/loadtest/           k6 scenario, 50 concurrent investigations
```

Google Cloud SDK usage must be visible in code, not only named in the README. `google-adk` and `google-genai` are imported and called in `agent-worker`. The MCP configuration is loaded in `mcp-gateway`. Both are checkable by an automated screen.

---

## 16. Build Sequence

Order by demo risk, highest first. Everything must be created inside the contest period, so start from an empty repository.

| Days | Deliverable | Why this order |
|---|---|---|
| 1 to 3 | `render-sim` with one world, OTel export verified in Grafana Cloud | Nothing else is testable until real telemetry exists |
| 4 to 6 | `mcp-gateway` with allowlist, cache, singleflight, call log | The concurrency story lives here and every other service depends on it |
| 7 to 9 | `agent-worker` investigation loop, single user, end to end | Proves the core claim before any scaling work |
| 10 to 11 | `impact-engine` plus Cloud SQL production metadata | Turns an SRE demo into a production demo |
| 12 to 13 | Approval flow, `action-executor`, verification loop | Closes the loop, which is the strongest demo moment |
| 14 | Multi-tenant leasing, observer mode, k6 load test at 50 | Validates the architecture claim with a number |
| 15 to 16 | Web UI polish, agent observability dashboard | Design is 25 percent of the score |
| 17 | Three-minute video, README, hosted deploy, license check | Only the first three minutes are evaluated |

Cut multi-agent decomposition if the schedule slips. One agent with a disciplined tool loop demonstrates more competence than three agents passing messages.

---

## 17. Submission Compliance Checklist

- [ ] Hosted project URL, reachable and functional
- [ ] Public repository with an open-source license file detectable in the About section
- [ ] Google Cloud SDK imported and called at runtime: `google-adk` and `google-genai` in `agent-worker`
- [ ] Grafana Cloud MCP connection loaded and exercised at runtime in `mcp-gateway`
- [ ] No third-party AI models, agent frameworks, or AI APIs anywhere in the codebase
- [ ] Project created entirely within the contest period, with commit history to show it
- [ ] Demo video at or under three minutes, English or English subtitled, public on YouTube or Vimeo
- [ ] Runs on web
- [ ] Text description covering features, technologies, data sources, findings, and learnings
- [ ] Team of four or fewer, all listed on Devpost
- [ ] Submitted before 9 September 2026, 14:00 PT

---

## 18. Open Decisions

- Confirm exact Grafana MCP tool names and which categories require explicit enabling on the server.
- Confirm whether Grafana Assistant counts as a permitted built-in partner AI feature. The architecture does not depend on it, but the answer should be settled before the writeup claims a constraint.
- Decide Cloud SQL versus Firestore for production metadata. Cloud SQL is assumed here for the relational join.
- Decide whether observer mode users see other users' activity or a private replay of the shared world.
- Set the Grafana Cloud plan and confirm the series allowance against the cardinality budget.
- Confirm the Mimir out-of-order ingestion window on the chosen plan.
