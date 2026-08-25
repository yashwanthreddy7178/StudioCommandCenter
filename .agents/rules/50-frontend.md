---
trigger: glob
globs: web/**
description: React SPA standards. Design is 25 percent of the judging score.
---

# Frontend

## What the UI has to prove

Judges score design on whether this is a coherent product rather than a technical proof
of concept. A chat transcript is a proof of concept. The interface must show production
state, not just agent output.

Non-negotiable surfaces:

- **Production board.** Shots with status, priority, sequence, and the delivery
  countdown. This is the home view, not a side panel.
- **Projected completion time.** Rendered prominently and updated live. It moves when
  the incident starts and moves back after the approved rollback. This single number is
  the demo.
- **Evidence ledger.** Each investigation step as it streams: which tool, which
  parameters, what came back, cache hit or miss. Judges need to see the agent choosing
  its own path.
- **Hypothesis panel.** The six tests with pass or fail per test, and the resulting
  confidence. Never a bare confidence number.
- **Approval modal.** Ranked options with recovery estimate, risk, and production
  consequence. Explicit action, disabled while a run is executing.
- **Degraded and stale banners.** When evidence is stale or a run halted degraded, the
  UI says so plainly.

## Technical

- React 18, TypeScript strict, functional components with hooks. No class components.
- Server state through the SSE stream and a typed event reducer. No polling.
- Types for API payloads are generated from the backend schema, never hand-copied.
- Tailwind for styling. No CSS-in-JS, no component library that imposes its own look.
- Reconnect on SSE drop by re-reading the run document and resuming from the last event
  index. Never lose a run because a laptop slept.
- Loading and error states for every async surface. No spinner that lasts forever.

## Never

- Never fake data to make a screen look complete. An empty state is honest, a fabricated
  shot list is not.
- Never animate a number changing without the backing value having changed.
- Never hide a degraded state to keep the demo looking clean.
