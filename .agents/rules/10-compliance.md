---
trigger: always_on
description: Hackathon submission constraints. Violating any of these disqualifies the project.
---

# Submission compliance

The Agentic Cinema rules are hard constraints. A violation costs the whole submission,
not a few points. Treat these as compile errors.

## AI tooling restriction

Only Google Cloud AI services and built-in Grafana AI features are permitted. No other
model providers, agent frameworks, or AI APIs. Concretely, none of these may appear in
any dependency file, import, config, or HTTP call:

- OpenAI, Anthropic, Cohere, Mistral, xAI, Together, Groq, Replicate
- LangChain, LlamaIndex, CrewAI, AutoGen, Haystack, Semantic Kernel
- Hugging Face inference APIs, Ollama, local model runtimes
- Any embedding, reranking, or vector service backed by a non-Google model

Permitted for AI: `google-adk`, `google-genai`, `google-generativeai`,
`google-cloud-aiplatform`. Non-AI third-party libraries are fine (web frameworks,
database drivers, OTel, testing tools).

If a task seems to need a banned dependency, stop and raise it.

## Grafana usage must be real

The Grafana MCP connection must be loaded and exercised at runtime in `mcp-gateway`,
visible in code rather than only described in the README. Never replace a live MCP call
with a hardcoded fixture on the demo path. Test fixtures belong in tests only.

## Repository requirements

- Public repository, Apache-2.0 `LICENSE` at the root.
- No secrets in history. If you ever commit one, say so immediately rather than
  amending quietly.
- All source, assets, and setup instructions needed to run the project must be present.
- Commit history must show the work happening inside the contest period. Do not
  backdate commits, squash the history into one commit, or import code from an older
  repository.

## New work only

Every file is written during the contest period. Do not copy in code from a previous
project. Do not scaffold from a private template. Public open-source dependencies
installed normally are fine.

## Deliverables the code must support

- The project runs on web and is reachable at a hosted URL.
- The demo path completes in under three minutes end to end, because only the first
  three minutes of the video are evaluated. If a change pushes the happy path past that,
  flag it.
