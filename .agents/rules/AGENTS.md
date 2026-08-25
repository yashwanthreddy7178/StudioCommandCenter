# Studio Production Commander

Gemini agent that investigates render-pipeline incidents through the Grafana Cloud MCP
server and translates them into VFX delivery-deadline impact. Hackathon submission for
Agentic Cinema, Grafana Labs track. Deadline 9 September 2026, 14:00 PT.

## Product shape in one line

Grafana tells us what is happening in the infrastructure. This agent tells the studio
what it means for the production, what to do about it, and whether the fix worked.

## Stack

- Python 3.12, FastAPI, `google-adk` + `google-genai` for the agent
- React + TypeScript + Vite for the UI
- Cloud Run for all services, Pub/Sub for run dispatch
- Firestore for run state, Cloud SQL Postgres for production metadata
- Memorystore Redis for cache, locks, tenant leases, rate limiting
- Grafana Cloud (Mimir, Loki, Tempo) reached only through the Grafana MCP server
- OpenTelemetry for both simulated render telemetry and agent self-instrumentation
- Terraform for infrastructure

## Services

| Path | Role |
|---|---|
| `services/api-gateway` | Auth, tenant leasing, run creation, approval intake. Stateless. |
| `services/stream-service` | SSE fan-out of run events to browsers |
| `services/agent-worker` | ADK investigation loop. The only service calling Gemini. |
| `services/mcp-gateway` | The only service calling Grafana MCP. Cache, dedupe, rate limit, allowlist. |
| `services/impact-engine` | Deterministic production impact. No model dependency. |
| `services/action-executor` | Executes approved remediations. Idempotent. Audited. |
| `services/render-sim` | Multi-tenant render farm simulator. Emits OTel. |
| `web` | React SPA |
| `infra/terraform` | All infrastructure |
| `docs` | Architecture specification |

## Load target

50+ concurrent users. 24 writable tenant worlds plus an unbounded read-only observer
world. Concurrency correctness is a functional requirement, not an optimization.

## Where the detail lives

Full architecture, data model, capacity targets, and failure modes:
`docs/architecture.md`. Read it before proposing structural changes.
