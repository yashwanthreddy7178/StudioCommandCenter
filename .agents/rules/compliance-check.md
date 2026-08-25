---
description: Audit the repository against the Agentic Cinema submission requirements.
---

# Compliance check

Run this before any push to main and again the day before submission. Report findings as
a checklist with pass or fail per item. Do not fix anything without asking first.

## Steps

### 1. Banned AI dependencies

Search every dependency file, lockfile, import statement, and outbound URL for: openai,
anthropic, claude, cohere, mistral, xai, groq, together, replicate, huggingface, ollama,
langchain, llamaindex, crewai, autogen, haystack, semantic-kernel.

Report every hit with its file and line. Any hit is a submission-level failure.

### 2. Required Google Cloud usage

Confirm at least one of `google-adk`, `google-genai`, `google-generativeai`, or
`google-cloud-aiplatform` is both declared as a dependency and actually imported and
called in `services/agent-worker`. Naming it in the README does not count.

### 3. Grafana MCP usage

Confirm `services/mcp-gateway` loads the MCP configuration and issues real calls at
runtime. Confirm no other service opens an MCP connection. Confirm no Assistant-native
tool name appears anywhere in the codebase.

### 4. Secrets

Scan tracked files and full git history for API keys, service account JSON, tokens, and
connection strings. Confirm `.env` is gitignored and `.env.example` contains only
placeholders. Report any finding immediately and do not attempt a quiet fix.

### 5. Repository requirements

Confirm an Apache-2.0 `LICENSE` exists at the repository root. Confirm the README
contains setup instructions sufficient for a judge to run the project from scratch.
Confirm the commit history sits inside the contest period and is not a single squashed
commit.

### 6. Runtime

Confirm `/healthz` and `/readyz` respond on every service. Confirm the hosted URL loads
and the happy path completes end to end in under three minutes.

### 7. Report

Output a table: item, pass or fail, evidence, and what to do about each failure. Order
failures by severity, submission-level first.
