---
trigger: always_on
description: Non-negotiable invariants for Studio Production Commander. Always applied.
---

# Core invariants

These hold everywhere. If a task appears to require breaking one, stop and say so
instead of working around it.

## Boundaries you do not cross

1. **Only `mcp-gateway` talks to Grafana MCP.** No other service imports an MCP client
   or opens an MCP connection. If a service needs Grafana data, it calls the gateway.
2. **Only `agent-worker` talks to Gemini.** No other service imports `google-genai` or
   `google-adk`.
3. **Only `action-executor` writes to the render control plane.** Nothing else mutates
   world state.
4. **The model never does arithmetic that reaches the user.** Delay minutes, shot
   counts, throughput, recovery estimates all come from `impact-engine`. If you find
   yourself prompting a model to compute a number, that is a bug.
5. **No write action without recorded human approval.** No timeout auto-approves. No
   "safe enough to skip" path. No debug flag that bypasses it.

## Truthfulness in output

- When evidence is missing, the agent says what is missing. It never fills the gap with
  a plausible-sounding claim.
- Confidence is derived from named passing tests, never asserted by the model.
- Every user-facing number carries the method that produced it.
- Never fabricate telemetry, cache a fake response, or stub a Grafana result to make a
  demo path work. If real data is unavailable, surface the degraded state.

## Before you write code

- Read `docs/architecture.md` for the area you are touching.
- State the plan first for anything spanning more than one file. Wait for approval.
- Match existing patterns in the service you are editing. Do not introduce a second way
  to do something already done.

## Scope discipline

- Change what was asked. No opportunistic refactors, no drive-by renames, no reformatting
  files you are not otherwise editing.
- Do not add dependencies without saying why in the same message.
- Do not create new top-level directories.
- Never delete or rewrite a test to make it pass.

## Things that are always wrong here

- Secrets, tokens, or connection strings in source, config, or committed env files. The
  repository is public. Everything sensitive goes through Secret Manager.
- `time.sleep` in request handlers.
- Bare `except:` or `except Exception: pass`.
- Catching an error and returning a fabricated success shape.
- Mutable default arguments.
- A new library when the stdlib or an existing dependency covers it.
