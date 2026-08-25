---
trigger: glob
globs: services/mcp-gateway/**
description: The single choke point for Grafana MCP traffic. Carries the concurrency design.
---

# MCP gateway

This service is why 50 concurrent users works. Every change here is load bearing.

## Required order of operations

Do not reorder. Each step depends on the one before.

1. **Allowlist check.** Reject any tool name outside `MCP_ALLOWLIST` and raise an alert.
   This is the compliance boundary and it is enforced here in code, never by a prompt.
2. **Tenant injection.** Add the `tenant_id` matcher to every PromQL and LogQL query,
   server-side, after the model has produced the query. A caller cannot opt out.
3. **Normalize and quantize.** Snap query time ranges to 15-second buckets so
   near-simultaneous identical questions produce one cache key.
4. **Cache lookup.** Key is `sha256(tool, normalized_params, tenant_id, time_bucket)`.
   Redis TTL 20s for range queries, 300s for metadata queries.
5. **Singleflight.** Concurrent identical keys collapse to one upstream call. The rest
   await the same future.
6. **Token bucket.** A Redis-backed global QPS ceiling, independent of instance count.
   Overflow waits up to 5 seconds, then returns the freshest cached result marked stale.
7. **Call, then cache the result.**

## Never

- Never a per-instance cache or per-instance rate limiter. Instance count varies, so
  in-process limits do not bound upstream QPS.
- Never let an allowlist rejection pass through as a normal error. It is a rule
  violation and must be visible.
- Never return a cached result without a freshness marker the UI can display.
- Never bypass the gateway from another service, including in tests that hit the real
  MCP server.

## Call log

Every call records tool name, parameters, latency, cache status, tenant, and run id.
This log is both the compliance artifact and an on-screen demo element, so the schema is
stable. Adding a field is fine. Removing or renaming one breaks the UI.

## Tool allowlist

Tool names are verified against the current Grafana MCP tools reference before use.
Some categories are disabled by default on the server and must be enabled explicitly.
If a tool call fails with an unknown-tool error, verify against the reference rather
than guessing a similar name.

## Degradation

When Grafana MCP rate limits or returns 5xx: serve stale cache up to 120 seconds and
mark the evidence stale. When the circuit breaker opens: return a typed unavailable
result so the run halts in `DEGRADED` with partial evidence. Never synthesize a
plausible response.

## Performance targets

Cache hit ratio above 85 percent under 50 concurrent investigations. Upstream QPS under
25. If a change moves either number the wrong way, say so.
