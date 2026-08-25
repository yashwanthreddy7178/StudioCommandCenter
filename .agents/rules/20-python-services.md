---
trigger: glob
globs: services/**/*.py
description: Python service standards for every Cloud Run service.
---

# Python service standards

## Shape

- Python 3.12. Full type hints on every function signature. `from __future__ import
  annotations` at the top of each module.
- Pydantic models for every request body, response body, and cross-service payload. No
  bare dicts across a service boundary.
- `async def` for anything doing IO. Never call a blocking client inside an async
  handler; use the async client or `asyncio.to_thread`.
- One concern per module. A file over 400 lines needs splitting.
- Structured logging only: `logger.info("event_name", extra={...})`. No f-strings in log
  messages, no `print`.

## Every service exposes

- `GET /healthz` returning 200 when the process is up
- `GET /readyz` checking its own dependencies
- OpenTelemetry tracing, with the run id on the span when one exists

## Stateless by default

Services hold no in-process state that matters across requests. The single exception is
`render-sim`, which holds world state and mirrors it to Firestore every 10 seconds.
Do not add caches, counters, or session dicts to module scope anywhere else. Cloud Run
scales to many instances and in-process state silently breaks under load.

## Configuration

- All config from environment variables, read once at startup into a typed settings
  object.
- No default values for secrets. A missing secret must crash at startup, loudly, not
  fall back.
- Never read a secret at request time.

## Errors

- Raise typed domain exceptions and map them to HTTP status at the edge.
- Never swallow an exception to keep a request alive. Degraded responses must be
  explicitly modelled and labelled as degraded, so the UI can show it.
- Log the failure with enough context to find the run: run id, tenant id, tool name.

## External calls

Every outbound call needs an explicit timeout, bounded retry with jittered backoff, and
a circuit breaker where the dependency is shared. No unbounded retry loops. No retry on
a non-idempotent write.

## Tests

- `pytest` with `pytest-asyncio`. Tests live beside the service in `tests/`.
- Every bug fix starts with a failing test.
- `impact-engine` requires 100 percent branch coverage. Its output is shown to judges
  and must be reproducible.
- Mock at the network boundary, not by patching internal functions.
