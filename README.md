# Studio Production Commander

**AI-Augmented VFX Production Incident Investigation & Delivery Protection**

*Hackathon Submission for Agentic Cinema: The Blockbuster Hackathon (Grafana Labs Track)*

---

## 🎬 Overview

Grafana Cloud tells us what is happening in the rendering infrastructure. **Studio Production Commander** translates infrastructure telemetry into VFX delivery-deadline impact, proposes ranked remediations, gates writes behind human approval, executes idempotent remediations on the render control plane, and verifies recovery.

### Key Capabilities
- **Gemini ADK Autonomous Investigation Loop**: Investigates anomalies through Grafana MCP using a dynamic planning and tool-selection loop, discovering the farm's metric names for itself rather than being handed them.
- **The Agent Is Itself Observable**: Every LLM call, tool invocation, token count and MCP round trip is exported as OpenTelemetry GenAI spans to the same Grafana stack the agent queries. The agent can be debugged exactly the way it debugs the render farm.
- **Bi-directional Grafana Integration**: Approved remediations are written back — an annotation on the Grafana timeline, and an incident when a delivery is at risk — so the people who own the dashboard see what happened.
- **Deterministic Impact Engine**: Calculates exact frame delays, affected shots, high-priority sequences, and projected delivery time shifts without model arithmetic.
- **Falsifiable Hypothesis Testing**: Scores findings against scientific criteria (temporal precedence, metric correlation, mechanism, localization, trace attribution, control group). A criterion the connected server cannot supply evidence for is reported as skipped rather than failed, so the deployment's tool inventory never masquerades as a weak investigation.
- **Multi-Tenant Load Control**: 24 isolated writable worlds plus observer mode, server-side tenant injection, time quantization, content-addressed caching and singleflight deduplication, so many concurrent investigations share one Grafana and Gemini quota. Runs as a single instance; see *Deployment model and state*.
- **Audited Human-in-the-Loop Remediation**: Gated execution with idempotency keys and post-action Grafana verification loops.

---

## 🏗️ Architecture

```
                    Browser (React SPA + Tailwind)
                                |
                     HTTPS + SSE (JWT bearer)
                                |
        +-----------------------+-----------------------+
        |                       |                       |
        v                       v                       v
  +-------------+       +----------------+       +------------+
  | api-gateway |       | stream-service |       | web (SPA)  |
  | tenant lease|       | SSE fan-out    |       |            |
  +-------------+       +----------------+       +------------+
        |                       |
        | start run             | polls /runs/{id}/events
        v                       v
  +------------------------------------------+
  | agent-worker  (Google ADK + Gemini)      |
  | investigation loop, hypothesis scoring   |
  | in-process run / evidence / event store  |
  +------------------------------------------+
        |               |                  |
        v               v                  v
  +-------------+ +---------------+ +-----------------+
  | mcp-gateway | | impact-engine | | action-executor |
  | allowlist,  | | deterministic | | approval gate,  |
  | cache,      | | delivery      | | audit trail,    |
  | tenant inj. | | projection    | | verification    |
  +-------------+ +---------------+ +-----------------+
        |               |                  |
        |               v                  v
        |         +-----------+     +-------------+
        |         | SQLite or |     | render-sim  |
        |         | Postgres  |     | control     |
        |         | metadata  |     | plane       |
        |         +-----------+     +-------------+
        |                                  |
        | queries (read)                   | OTLP metrics,
        |                                  | logs, traces
        |          approved writes         |
        |          (annotations,           |
        |           incidents)             |
        v                                  v
  +--------------------------------------------------------+
  |                    Grafana Cloud                        |
  |   Mimir / Loki / Tempo  --  render farm telemetry       |
  |   Agent Observability   --  the agent's own GenAI spans |
  +--------------------------------------------------------+
```

### Deployment model and state

State is deliberately in-process. The run store, evidence ledger, event stream,
audit trail, approval idempotency keys and tenant leases all live in memory in
the service that owns them, and `stream-service` reads run events from
`agent-worker` over HTTP rather than from a shared store. `mcp-gateway` uses Redis
when `REDIS_HOST` resolves and falls back to an in-process cache when it does not;
`impact-engine` defaults to in-memory SQLite and accepts a Postgres URL instead.

The consequence is that the system runs as a **single instance**: it needs no
Firestore, no Pub/Sub and no Memorystore to start, which makes it trivial to run
locally and to deploy as one container, but it does not scale horizontally as
written. The 24-world tenant isolation, time quantization, content-addressed
caching and singleflight deduplication are all real and all in the request path;
they bound load against Grafana and Gemini within one instance rather than
distributing it across many.

`infra/terraform/` provisions Firestore, Pub/Sub, Memorystore and Cloud SQL for
the scale-out path. **These are not wired into the code yet** and the manifests
have not been applied.

### Microservices Summary

| Directory | Service | Runtime | Responsibility |
|---|---|---|---|
| `services/render-sim` | Render Farm Simulator | FastAPI / Python 3.12 | Discrete-event multi-tenant simulator (24 worlds), Blender Open Data grounded, OTel exporter. |
| `services/mcp-gateway` | MCP Gateway Proxy | FastAPI / Python 3.12 | Chokepoint for Grafana MCP. Tool allowlist, Redis caching, singleflight, tenant injection. |
| `services/impact-engine` | Deterministic Impact Engine | FastAPI / Python 3.12 | Pure deterministic production impact calculations and Cloud SQL Postgres metadata joins. |
| `services/agent-worker` | Agent Worker | Python 3.12 / Google ADK | Gemini investigation loop, hypothesis scoring, evidence ledger, GenAI trace export. |
| `services/action-executor` | Action Executor | FastAPI / Python 3.12 | Gated idempotent execution of approved actions, in-process audit trail, Grafana write-back, verification loop. |
| `services/api-gateway` | API Gateway | FastAPI / Python 3.12 | Anonymous JWT auth, in-process tenant lease pool manager, run creation and approval intake. |
| `services/stream-service`| Stream Service | FastAPI / Python 3.12 | SSE fan-out to browsers, polling run events from agent-worker. |
| `web` | Web Application | React 18 / TypeScript / Vite | Production Board, Delivery Countdown, Live Evidence Ledger, Hypothesis Matrix, Approval Modal. |

---

## 🔭 Grafana Cloud Integration

The Grafana integration is two halves, and this project implements both.

### 1. The agent reads production through Grafana MCP

Investigation queries go out over MCP to Prometheus and Loki. Nothing is
synthesised: if Grafana is unreachable the run is reported degraded rather than
answered from a fabricated payload, because evidence the agent cannot distinguish
from real telemetry makes every downstream finding worthless.

`services/mcp-gateway` is the chokepoint. It enforces a tool allowlist, injects
the tenant matcher and the datasource UID server-side so neither is reachable from
the model, caches on content address, and collapses duplicate in-flight calls.

**Why the self-hosted MCP server, not the hosted one.** Grafana Cloud's hosted MCP
server authenticates over OAuth 2.1, which needs an interactive browser consent a
headless Cloud Run worker cannot complete. ADK compounds this by opening several
MCP sessions at once, each starting its own browser flow and contending for the
same callback port. Running `grafana/mcp-grafana` ourselves lets it authenticate
with a service account token instead, and the single pooled session in
`mcp-gateway` sidesteps the multi-session problem by construction. The hackathon's
Grafana track accepts either the official `grafana/mcp-grafana` server or the
hosted endpoint.

### 2. The agent writes back, and is itself observed

**Write-back.** Once a supervisor approves a remediation, `action-executor` marks
the Grafana timeline with what changed and why, and opens a Grafana incident when
a deliverable is at risk. These are best-effort: the control plane has already
been changed by then, so a Grafana outage is recorded and reported but never turns
an applied rollback into a reported failure.

This needs `annotations:create` and the incident write permission on the Grafana
service account; `scripts/verify_live.py` reports exactly which are missing.

Write tools are reachable **only** through the gateway's `/write` endpoint, which
requires an approval id. The agent's query path validates against an allowlist
containing no write tool at all, so no prompt injection in telemetry can reach
Grafana with a write. This is asserted per tool in
`services/mcp-gateway/tests/test_gateway.py`.

**Agent observability.** ADK emits GenAI-semantic-convention spans through the
global tracer provider, so installing one with an OTLP exporter
(`services/common/tracing.py`) is the entire integration: agent invocations, LLM
calls with token usage, and every tool call land in Grafana Cloud. On top of that
the planner adds a root `investigation` span per run and one span per MCP call
carrying tool name, latency, and whether the gateway served it from cache.

Set `GRAFANA_OTLP_ENDPOINT_URL`, `GRAFANA_OTLP_INSTANCE_ID` and
`GRAFANA_ACCESS_POLICY_TOKEN` to enable. Without them every span is a cheap no-op
and the services still run.

### Tempo

`render-sim` exports one frame-render trace per worker per interval, split into
`fetch_assets`, `gpu_render` and `write_output`. The fixed costs stay fixed while
GPU time absorbs a frame's slowdown, which is what lets a trace rule out storage
and the control API as the cause rather than merely not mentioning them.

The agent queries those traces through `tempo_traceql-search`, and the
trace-attribution criterion is scored from what comes back. Against a server that
does not expose trace search, set `TEMPO_SEARCH_AVAILABLE=false` and the criterion
reports as skipped rather than failed, so a missing tool never masquerades as a
weak investigation.

---

## 🚀 Quickstart & Setup

### Prerequisites
- Python 3.12+
- Node.js 20+ & npm
- A Grafana Cloud stack (free tier is sufficient)
- Redis is **optional**: `mcp-gateway` falls back to an in-process cache without it

### 1. Clone and configure

```bash
git clone https://github.com/yashwanthreddy7178/StudioCommandCenter.git
cd StudioCommandCenter
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -e services/agent-worker -e services/mcp-gateway \
            -e services/impact-engine -e services/action-executor \
            -e services/render-sim -e services/api-gateway \
            -e services/stream-service

cp .env.example .env
# Fill in GOOGLE_CLOUD_PROJECT (or GEMINI_API_KEY), GRAFANA_STACK_URL,
# GRAFANA_SERVICE_ACCOUNT_TOKEN, and the three GRAFANA_OTLP_* ingest values.
```

### 2. Start the Grafana MCP server

Everything else depends on this. It is a separate process, not one of the
services, and it is what `mcp-gateway` talks to.

```bash
python scripts/fetch_mcp_grafana.py    # downloads the binary into .tools/ (once)
python scripts/run_mcp_grafana.py      # serves MCP on http://localhost:8081/mcp
```

### 3. Start the services

```bash
python scripts/start_all.py            # all seven services with the right paths
```

Or individually, from each service directory, with the repository root on
`PYTHONPATH` so `services.common.*` resolves:

| Service | Port |
|---|---|
| `api-gateway` | 8000 |
| `mcp-gateway` | 8001 |
| `impact-engine` | 8002 |
| `action-executor` | 8003 |
| `render-sim` | 8004 |
| `stream-service` | 8005 |
| `agent-worker` | 8010 |

```bash
cd services/render-sim && uvicorn src.main:app --port 8004
```

### 4. Start the web app

```bash
cd web && npm install && npm run dev
```

### 5. Trigger the scenario

```bash
curl -X POST http://localhost:8004/scenario/trigger-incident \
  -H "Content-Type: application/json" \
  -d '{"tenant_id": "t01"}'
```

Then start an investigation from the web UI. Telemetry needs roughly one export
interval (15s) to reach Grafana before the agent can find it.

---

## 🧪 Testing & Verification

Each service pins its own `src` package, so the suites are run **one service at a
time**; pointing pytest at two service directories at once makes `src` ambiguous
and collection fails.

```bash
pytest services/render-sim/tests
pytest services/mcp-gateway/tests
pytest services/impact-engine/tests
pytest services/action-executor/tests
pytest services/agent-worker/tests
pytest services/api-gateway/tests
pytest services/stream-service/tests

# Submission compliance check: banned dependencies, required Google AI SDK,
# Grafana MCP isolation, license, and commit history.
python scripts/compliance_audit.py
```

---

## ⚖️ License & Compliance

This project is licensed under the **Apache-2.0 License**. See [LICENSE](LICENSE) for details.

**Agentic Cinema Hackathon Compliance**:
- Built strictly using **Google Cloud AI (`google-adk`, `google-genai`)** and **Grafana Cloud MCP**.
- Zero third-party AI model dependencies or banned frameworks.
- Zero secrets committed to version control.
- Designed to complete the core demo investigation in under 3 minutes.
