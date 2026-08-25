# Studio Production Commander

**AI-Augmented VFX Production Incident Investigation & Delivery Protection**

*Hackathon Submission for Agentic Cinema: The Blockbuster Hackathon (Grafana Labs Track)*

---

## 🎬 Overview

Grafana Cloud tells us what is happening in the rendering infrastructure. **Studio Production Commander** translates infrastructure telemetry into VFX delivery-deadline impact, proposes ranked remediations, gates writes behind human approval, executes idempotent remediations on the render control plane, and verifies recovery.

### Key Capabilities
- **Gemini ADK Autonomous Investigation Loop**: Investigates anomalies through Grafana Cloud MCP (Mimir, Loki, Tempo) using a dynamic planning and tool-selection loop.
- **Deterministic Impact Engine**: Calculates exact frame delays, affected shots, high-priority sequences, and projected delivery time shifts without model arithmetic.
- **Falsifiable Hypothesis Testing**: Scores findings against 6 scientific criteria (temporal precedence, metric correlation, mechanism, localization, trace attribution, control group).
- **High-Throughput Concurrency (50+ Concurrent Users)**: Multi-tenant world isolation (24 writable worlds + observer mode), time quantization, content-addressed Redis caching, and singleflight deduplication achieving >85% cache hit rates.
- **Audited Human-in-the-Loop Remediation**: Gated execution with idempotency keys and post-action Grafana verification loops.

---

## 🏗️ Architecture

```
                       Browser (React SPA + Tailwind)
                                     |
                          HTTPS + SSE (JWT bearer)
                                     v
                       +---------------------------+
                       | Global Load Balancer / LB |
                       +---------------------------+
                        /            |            \
                       /             |             \
                      v              v              v
               +-------------+ +----------------+ +------------+
               | api-gateway | | stream-service | | web (SPA)  |
               +-------------+ +----------------+ +------------+
                      |                 ^
                      | enqueue run     | run events (SSE)
                      v                 |
                 +---------+     +-------------+
                 | Pub/Sub |---->|  Firestore  |<-----+
                 +---------+     |  run state  |      |
                      |          +-------------+      |
                      v                               |
             +----------------------------------+     |
             | agent-worker (ADK + Gemini)      |-----+
             | investigation loop & hypothesis  |
             +----------------------------------+
                /             |              \
               v              v               v
        +-------------+ +---------------+ +-----------------+
        | mcp-gateway | | impact-engine | | action-executor |
        +-------------+ +---------------+ +-----------------+
               |              |                   |
               v              v               v
        +-------------+ +---------------+ +-----------------+
        | Grafana MCP | | Cloud SQL PG  | | render-sim      |
        | (Mimir/Loki/| | (Production   | | (Control Plane  |
        |  Tempo)     | |  Metadata)    | |  & Telemetry)   |
        +-------------+ +---------------+ +-----------------+
```

### Microservices Summary

| Directory | Service | Runtime | Responsibility |
|---|---|---|---|
| `services/render-sim` | Render Farm Simulator | FastAPI / Python 3.12 | Discrete-event multi-tenant simulator (24 worlds), Blender Open Data grounded, OTel exporter. |
| `services/mcp-gateway` | MCP Gateway Proxy | FastAPI / Python 3.12 | Chokepoint for Grafana MCP. Tool allowlist, Redis caching, singleflight, tenant injection. |
| `services/impact-engine` | Deterministic Impact Engine | FastAPI / Python 3.12 | Pure deterministic production impact calculations and Cloud SQL Postgres metadata joins. |
| `services/agent-worker` | Agent Worker | Python 3.12 / Google ADK | Gemini 2.5/3.0 investigation loop, hypothesis scoring, evidence ledger. |
| `services/action-executor` | Action Executor | FastAPI / Python 3.12 | Gated idempotent execution of approved actions, Firestore audit trail, verification loop. |
| `services/api-gateway` | API Gateway | FastAPI / Python 3.12 | Anonymous JWT auth, Redis tenant lease pool manager, run creation. |
| `services/stream-service`| Stream Service | FastAPI / Python 3.12 | High-concurrency SSE stream fan-out from Firestore run event snapshots. |
| `web` | Web Application | React 18 / TypeScript / Vite | Production Board, Delivery Countdown, Live Evidence Ledger, Hypothesis Matrix, Approval Modal. |

---

## 🚀 Quickstart & Setup

### Prerequisites
- Python 3.12+
- Node.js 20+ & npm
- Docker (optional for containerized execution)
- Redis server (local or cloud)

### 1. Clone & Configure Environment
```bash
git clone https://github.com/your-org/StudioCommandCenter.git
cd StudioCommandCenter
cp .env.example .env
# Edit .env with your Google Cloud and Grafana credentials
```

### 2. Run Locally
Each service can be run locally or via docker-compose:
```bash
# Start Redis
redis-server &

# Run Render Simulator
cd services/render-sim && uvicorn src.main:app --port 8004 &

# Run MCP Gateway
cd services/mcp-gateway && uvicorn src.main:app --port 8001 &

# Run Impact Engine
cd services/impact-engine && uvicorn src.main:app --port 8002 &

# Run Action Executor
cd services/action-executor && uvicorn src.main:app --port 8003 &

# Run API Gateway & Stream Service
cd services/api-gateway && uvicorn src.main:app --port 8000 &
cd services/stream-service && uvicorn src.main:app --port 8005 &

# Run Frontend Web App
cd web && npm install && npm run dev
```

---

## 🧪 Testing & Verification

```bash
# Run pytest across all services
pytest services/render-sim/tests
pytest services/mcp-gateway/tests
pytest services/impact-engine/tests
pytest services/action-executor/tests

# Run submission compliance check
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
