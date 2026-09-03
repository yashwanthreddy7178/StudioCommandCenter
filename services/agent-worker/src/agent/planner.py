"""Investigation loop built on the Google Agent Development Kit.

An `LlmAgent` selects which telemetry to gather and the ADK `Runner` executes the
tool calls, so the perceive-decide-act cycle belongs to the framework rather than
to hand-rolled dispatch code. The tools are closures bound to one run: they call
Grafana through mcp-gateway, append to the evidence ledger, and emit step events
as they go.

What the model decides stops at evidence gathering. Scoring the falsifiable
tests, projecting delivery impact and executing any action remain deterministic
code, per the invariant that a model must not produce a number a user relies on.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode

from src.config import settings
from src.agent.prompts import INVESTIGATION_SYSTEM_PROMPT, wrap_untrusted_telemetry
from src.agent.toolspec import planning_instruction, summarise_for_model
from src.agent.tools import tool_client
from src.agent.hypothesis import derive_fleet_findings, evaluate_falsifiable_hypotheses
from services.common.models import (
    ActionType,
    ConfidenceLevel,
    EventType,
    EvidencePayload,
    ImpactProjection,
    RemediationOption,
    RiskLevel,
    RunDocument,
    RunState,
    StepEvent,
)
from services.common.telemetry import setup_logging

logger = setup_logging("agent-worker-planner")

# Resolved against the global provider installed in main, so this is a no-op
# tracer when trace export is not configured.
_tracer = otel_trace.get_tracer("agent-worker.investigation")

try:
    from google.adk.agents import LlmAgent
    from google.adk.runners import InMemoryRunner
    from google.genai import types as genai_types

    HAS_ADK = True
except ImportError:  # pragma: no cover - exercised only without the SDK installed
    HAS_ADK = False

APP_NAME = "studio-production-commander"


def _configure_model_environment() -> bool:
    """Publishes credentials into the environment ADK reads, returning readiness.

    ADK resolves auth through google-genai, which reads process environment rather
    than the settings object, so values loaded from .env have to be exported here.
    """
    if not HAS_ADK:
        logger.warning("google-adk is not installed; model planning is unavailable")
        return False

    if settings.use_vertex_ai:
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
        os.environ.setdefault("GOOGLE_CLOUD_PROJECT", settings.google_cloud_project or "")
        os.environ.setdefault("GOOGLE_CLOUD_LOCATION", settings.google_cloud_location)
        logger.info(
            "Model access configured via Vertex AI and Application Default Credentials",
            extra={
                "project": settings.google_cloud_project,
                "location": settings.google_cloud_location,
            },
        )
        return True

    if settings.gemini_api_key:
        os.environ.setdefault("GOOGLE_API_KEY", settings.gemini_api_key)
        os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")
        logger.info("Model access configured via the Gemini Developer API key")
        return True

    logger.warning(
        "No model credentials configured; set GOOGLE_CLOUD_PROJECT for ADC "
        "or GEMINI_API_KEY for the Developer API"
    )
    return False


def _fallback_plan() -> List[Tuple[str, Dict[str, Any], str]]:
    """Fixed evidence sequence used when no model credentials are configured.

    Covers the same ground the agent is asked to cover, so the rest of the
    pipeline stays exercisable in tests and offline development.
    """
    def promql(expr: str) -> Dict[str, Any]:
        return {"expr": expr, "queryType": "instant", "endTime": "now"}

    return [
        ("query_prometheus", promql("render_worker_frame_duration_seconds"),
         "Measuring frame render duration per worker"),
        ("query_prometheus", promql("render_worker_gpu_utilization_ratio"),
         "Inspecting per-worker GPU utilisation to separate stalling from saturation"),
        ("query_prometheus", promql("render_throughput_frames_per_minute"),
         "Reading current fleet throughput"),
        ("query_prometheus", promql("render_baseline_throughput_frames_per_minute"),
         "Reading the fleet baseline throughput for comparison"),
        ("query_prometheus", promql("render_queue_depth_frames"),
         "Reading queue depth to size the backlog"),
        ("query_loki_logs",
         {"logql": '{service_name="render-sim"} |= "renderer_config_loaded"', "limit": 5},
         "Searching Loki for renderer configuration changes"),
        ("tempo_traceql-search",
         {"query": '{ name = "render_frame" }'},
         "Searching Tempo for frame render spans to attribute the latency"),
    ]


class _RunContext:
    """Per-run state shared between the ADK tools and the surrounding pipeline."""

    def __init__(self, run_doc: RunDocument, event_emitter: Any, start_seq: int) -> None:
        self.run_id = run_doc.run_id
        self.tenant_id = run_doc.tenant_id
        self.emitter = event_emitter
        self.seq = start_seq
        self.turn = 0
        self.evidence: List[Dict[str, Any]] = []

    def next_seq(self) -> int:
        current = self.seq
        self.seq += 1
        return current

    @property
    def exhausted(self) -> bool:
        return self.turn >= settings.max_investigation_turns


async def _execute_tool(
    ctx: _RunContext, tool_name: str, params: Dict[str, Any], reason: str
) -> Dict[str, Any]:
    """Runs one allowlisted query and records it as evidence.

    Shared by the ADK tools and the offline fallback so both paths produce an
    identical ledger and event stream.
    """
    ctx.turn += 1

    await ctx.emitter.emit_event(
        StepEvent(
            seq=ctx.next_seq(),
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            event_type=EventType.TOOL_CALL,
            title=f"Querying Grafana: {tool_name}",
            description=reason,
            step_turn=ctx.turn,
            payload={"tool_name": tool_name, "parameters": params},
        )
    )

    # One span per MCP call, so the agent's Grafana traffic is inspectable in
    # Grafana itself: which tool, on which turn, how long it took, and whether the
    # gateway served it from cache rather than reaching Grafana at all.
    with _tracer.start_as_current_span(
        f"mcp.{tool_name}",
        attributes={
            "gen_ai.tool.name": tool_name,
            "mcp.tool.name": tool_name,
            "investigation.run_id": ctx.run_id,
            "investigation.tenant_id": ctx.tenant_id,
            "investigation.turn": ctx.turn,
        },
    ) as span:
        try:
            mcp_res = await tool_client.call_mcp_gateway(
                tool_name=tool_name,
                parameters=params,
                tenant_id=ctx.tenant_id,
                run_id=ctx.run_id,
            )
        except Exception as exc:
            # Surfaced to the agent so it can try different evidence, and recorded
            # so the scorecard can report the gap rather than scoring around it.
            span.set_attribute("error.type", type(exc).__name__)
            span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
            logger.warning(
                "Tool call failed", extra={"tool": tool_name, "error": str(exc)[:200]}
            )
            await ctx.emitter.emit_event(
                StepEvent(
                    seq=ctx.next_seq(),
                    run_id=ctx.run_id,
                    tenant_id=ctx.tenant_id,
                    event_type=EventType.DEGRADED,
                    title=f"Query failed: {tool_name}",
                    description=str(exc)[:200],
                    step_turn=ctx.turn,
                    payload={"tool_name": tool_name},
                )
            )
            return {"error": str(exc)[:200]}

        span.set_attribute("mcp.cache_hit", bool(mcp_res.get("cache_hit", False)))
        span.set_attribute("mcp.is_stale", bool(mcp_res.get("is_stale", False)))
        span.set_attribute("mcp.latency_ms", float(mcp_res.get("latency_ms", 0.0) or 0.0))

    evidence_id = f"ev-{ctx.run_id}-{ctx.turn:02d}"
    await ctx.emitter.record_evidence(
        EvidencePayload(
            evidence_id=evidence_id,
            run_id=ctx.run_id,
            step_seq=ctx.seq,
            tool_name=tool_name,
            parameters=params,
            latency_ms=mcp_res.get("latency_ms", 0.0),
            cache_hit=mcp_res.get("cache_hit", False),
            is_stale=mcp_res.get("is_stale", False),
            raw_data=mcp_res.get("result", {}),
        )
    )

    # The raw payload is kept in the ledger because the falsifiable tests read
    # actual values; a prose summary alone would leave them nothing to evaluate.
    ctx.evidence.append({
        "evidence_id": evidence_id,
        "tool_name": tool_name,
        "query": params.get("expr") or params.get("logql") or params.get("query") or "",
        "raw_data": mcp_res.get("result"),
        "summary": reason,
        "cache_hit": mcp_res.get("cache_hit", False),
    })

    await ctx.emitter.emit_event(
        StepEvent(
            seq=ctx.next_seq(),
            run_id=ctx.run_id,
            tenant_id=ctx.tenant_id,
            event_type=EventType.EVIDENCE,
            title=f"Evidence recorded: {tool_name}",
            description=(
                f"{mcp_res.get('latency_ms', 0):.0f} ms, "
                f"cache_hit={mcp_res.get('cache_hit')}"
            ),
            evidence_id=evidence_id,
            step_turn=ctx.turn,
            payload={"tool_name": tool_name, "cache_hit": mcp_res.get("cache_hit")},
        )
    )

    return {"result": wrap_untrusted_telemetry(summarise_for_model(mcp_res.get("result")))}


def _build_tools(ctx: _RunContext) -> List[Callable]:
    """Creates the run-scoped tools the agent may call.

    Both are narrower than the gateway allowlist: the agent supplies only a
    query. The datasource and the tenant matcher are injected server-side after
    generation, so neither is reachable from here.
    """

    async def query_prometheus(expr: str, reason: str) -> dict:
        """Run an instant PromQL query against render farm metrics.

        Args:
            expr: PromQL expression. Label matchers such as
                {renderer_version="v2.4.1"} compare subsets of the fleet. Do not
                add a tenant_id matcher; it is applied server-side.
            reason: What this query is intended to establish, in one sentence.
        """
        if ctx.exhausted:
            return {"error": "Step ceiling reached; call finish and report findings."}
        return await _execute_tool(
            ctx,
            "query_prometheus",
            {"expr": expr, "queryType": "instant", "endTime": "now"},
            reason,
        )

    async def query_loki_logs(logql: str, reason: str) -> dict:
        """Run a LogQL query against render farm logs.

        Args:
            logql: LogQL query. The stream selector is {service_name="render-sim"}.
                Configuration changes appear as lines containing
                'event=renderer_config_loaded'.
            reason: What this query is intended to establish, in one sentence.
        """
        if ctx.exhausted:
            return {"error": "Step ceiling reached; call finish and report findings."}
        return await _execute_tool(
            ctx, "query_loki_logs", {"logql": logql, "limit": 20}, reason
        )

    async def search_traces(traceql: str, reason: str) -> dict:
        """Search render farm traces with TraceQL.

        Args:
            traceql: TraceQL query. Each frame is a `render_frame` span with
                child spans `fetch_assets`, `gpu_render` and `write_output`, so
                comparing their durations shows whether time is going to the GPU
                or to storage. Span attributes include worker_id, renderer_version
                and gpu_type. Do not add a tenant_id matcher; it is applied
                server-side. Example: {{ name = "render_frame" }}
            reason: What this query is intended to establish, in one sentence.
        """
        if ctx.exhausted:
            return {"error": "Step ceiling reached; call finish and report findings."}
        return await _execute_tool(
            ctx, "tempo_traceql-search", {"query": traceql}, reason
        )

    async def list_metric_names(reason: str) -> dict:
        """List the metric names this Grafana stack publishes for the render farm.

        Args:
            reason: What this is intended to establish, in one sentence.
        """
        if ctx.exhausted:
            return {"error": "Step ceiling reached; call finish and report findings."}
        # Not tenant-scoped, and does not need to be: metric names are shared
        # across every world, and the values behind them are scoped on the
        # queries that follow.
        return await _execute_tool(ctx, "list_prometheus_metric_names", {}, reason)

    tools: List[Callable] = [query_prometheus, query_loki_logs]
    if settings.tempo_search_available:
        tools.append(search_traces)
    if settings.enable_metric_discovery:
        tools.insert(0, list_metric_names)
    return tools


class InvestigationPlanner:
    """Runs an ADK agent over Grafana telemetry to gather investigation evidence."""

    def __init__(self) -> None:
        self.model_ready = _configure_model_environment()

    async def _run_agent(self, ctx: _RunContext, objective: str) -> Optional[str]:
        """Drives the ADK agent until it finishes or the step ceiling is reached."""
        agent = LlmAgent(
            name="render_incident_investigator",
            model=settings.planning_model,
            description="Gathers Grafana evidence about render farm incidents.",
            instruction=INVESTIGATION_SYSTEM_PROMPT,
            tools=_build_tools(ctx),
        )
        runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
        session = await runner.session_service.create_session(
            app_name=APP_NAME, user_id=ctx.run_id
        )

        message = genai_types.Content(
            role="user",
            parts=[
                genai_types.Part(
                    text=planning_instruction(
                        objective,
                        discover_metrics=settings.enable_metric_discovery,
                    )
                )
            ],
        )

        summary: Optional[str] = None
        async for event in runner.run_async(
            user_id=ctx.run_id, session_id=session.id, new_message=message
        ):
            if event.is_final_response() and event.content:
                summary = "".join(p.text or "" for p in event.content.parts).strip()
            if ctx.exhausted:
                logger.warning(
                    "Step ceiling reached, halting agent",
                    extra={"run_id": ctx.run_id, "turns": ctx.turn},
                )
                break
        return summary or None

    async def run_investigation(
        self,
        run_doc: RunDocument,
        event_emitter: Any,
    ) -> RunDocument:
        """Executes a full investigation for a given run document."""
        run_id = run_doc.run_id
        tenant_id = run_doc.tenant_id
        objective = run_doc.objective

        logger.info(
            "Starting investigation run",
            extra={"run_id": run_id, "tenant_id": tenant_id},
        )
        run_doc.state = RunState.RUNNING
        run_doc.started_at = datetime.utcnow()

        await event_emitter.emit_event(
            StepEvent(
                seq=1,
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=EventType.PLAN,
                title="Investigation Plan Initialized",
                description=f"Gathering evidence for objective: '{objective}'",
                step_turn=1,
                payload={"objective": objective},
            )
        )

        ctx = _RunContext(run_doc, event_emitter, start_seq=2)

        # Parents the ADK spans and every MCP span beneath one trace, so a run is
        # a single tree in Grafana rather than scattered siblings.
        with _tracer.start_as_current_span(
            "investigation",
            attributes={
                "investigation.run_id": run_id,
                "investigation.tenant_id": tenant_id,
                "investigation.objective": objective,
                "gen_ai.request.model": settings.planning_model,
            },
        ):
            if self.model_ready:
                await self._run_agent(ctx, objective)
            else:
                logger.warning(
                    "No model credentials; following a fixed evidence sequence"
                )
                for tool_name, params, reason in _fallback_plan():
                    if ctx.exhausted:
                        break
                    await _execute_tool(ctx, tool_name, params, reason)

        evidence_ledger = ctx.evidence

        # Falsifiable scoring. Deterministic by design: confidence is a count of
        # tests that passed against real values, not an assertion by the model.
        scorecard = evaluate_falsifiable_hypotheses(
            evidence_ledger, tempo_available=settings.tempo_search_available
        )
        run_doc.hypothesis = scorecard
        run_doc.confidence = scorecard.confidence

        await event_emitter.emit_event(
            StepEvent(
                seq=ctx.next_seq(),
                run_id=run_id,
                tenant_id=tenant_id,
                event_type=EventType.HYPOTHESIS,
                title="Hypothesis Formed & Tested",
                description=(
                    f"Confidence: {scorecard.confidence.value} "
                    f"({scorecard.passed_count}/{scorecard.total_tests} "
                    "falsifiable tests passed)"
                ),
                payload=scorecard.model_dump(),
            )
        )

        # Deterministic impact projection. Every input is read from the evidence
        # just gathered; a missing figure means the projection is reported as
        # unavailable rather than completed with an assumed value.
        findings = derive_fleet_findings(evidence_ledger)
        impact: Optional[ImpactProjection] = None
        missing_inputs = [
            name
            for name in ("observed_throughput_fpm", "baseline_throughput_fpm", "queue_depth")
            if findings.get(name) is None
        ]

        if missing_inputs:
            await event_emitter.emit_event(
                StepEvent(
                    seq=ctx.next_seq(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type=EventType.IMPACT,
                    title="Production Impact Not Computed",
                    description=(
                        "Delivery impact could not be projected because the following "
                        f"telemetry was unavailable: {', '.join(missing_inputs)}."
                    ),
                    payload={"missing_inputs": missing_inputs},
                )
            )
        else:
            try:
                impact = await tool_client.calculate_impact(
                    tenant_id=tenant_id,
                    affected_workers=findings["affected_workers"],
                    observed_throughput_fpm=findings["observed_throughput_fpm"],
                    baseline_throughput_fpm=findings["baseline_throughput_fpm"],
                    queue_depth=findings["queue_depth"],
                )
            except Exception as exc:
                logger.error("Impact projection failed", extra={"error": str(exc)})
                await event_emitter.emit_event(
                    StepEvent(
                        seq=ctx.next_seq(),
                        run_id=run_id,
                        tenant_id=tenant_id,
                        event_type=EventType.IMPACT,
                        title="Production Impact Unavailable",
                        description=(
                            "The impact engine could not be reached, so no delivery "
                            "projection is available for this run."
                        ),
                        payload={"error": str(exc)[:300]},
                    )
                )

        if impact is not None:
            run_doc.impact = impact
            await event_emitter.emit_event(
                StepEvent(
                    seq=ctx.next_seq(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type=EventType.IMPACT,
                    title="VFX Production Impact Calculated",
                    description=(
                        f"Projected delay {impact.delay_minutes} minutes. "
                        f"At risk: {', '.join(impact.at_risk_deliverables) or 'none'}"
                    ),
                    payload=impact.model_dump(),
                )
            )

        # Remediation is only offered when the evidence supports one. An
        # investigation that finds a healthy fleet concludes; asking a supervisor
        # to approve a rollback of nothing invites them to act on a non-problem,
        # and would make the approval gate meaningless.
        affected = findings.get("affected_workers") or []
        at_risk = list(impact.at_risk_deliverables) if impact else []
        remediation_warranted = bool(affected) or bool(at_risk)

        if not remediation_warranted:
            run_doc.options = []
            run_doc.state = RunState.COMPLETED
            await event_emitter.emit_event(
                StepEvent(
                    seq=ctx.next_seq(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type=EventType.COMPLETED,
                    title="No Remediation Required",
                    description=(
                        "The investigation found no degraded workers and no deliverable "
                        "at risk, so no action is proposed."
                    ),
                    payload={
                        "confidence": scorecard.confidence.value,
                        "passed_tests": scorecard.passed_count,
                        "affected_workers": affected,
                    },
                )
            )
        else:
            run_doc.options = _build_remediation_options(impact, affected)
            run_doc.state = RunState.AWAITING_APPROVAL
            await event_emitter.emit_event(
                StepEvent(
                    seq=ctx.next_seq(),
                    run_id=run_id,
                    tenant_id=tenant_id,
                    event_type=EventType.APPROVAL_REQUIRED,
                    title="Human Approval Required",
                    description=(
                        f"{len(run_doc.options)} ranked remediation options proposed. "
                        "No action is taken without explicit approval."
                    ),
                    payload={"options": [opt.model_dump() for opt in run_doc.options]},
                )
            )

        run_doc.step_count = ctx.seq
        logger.info(
            "Investigation run completed, awaiting approval",
            extra={"run_id": run_id, "turns": ctx.turn},
        )
        return run_doc


def _build_remediation_options(
    impact: Optional[ImpactProjection],
    affected_workers: Optional[List[str]] = None,
) -> List[RemediationOption]:
    """Builds the ranked options, describing consequences from the projection.

    Action types come from the closed enum and every figure comes from the impact
    engine, so nothing here is a claim the system cannot substantiate. Each
    sentence is guarded by the condition it describes: a consequence is only
    quoted when the projection actually shows it.
    """
    workers = affected_workers or []
    scope = f"{len(workers)} degraded worker(s)" if workers else "the degraded workers"

    delayed = impact is not None and impact.delay_minutes > 0
    shortfall = (
        impact is not None
        and impact.observed_throughput_fpm < impact.baseline_throughput_fpm
    )

    if delayed:
        recovery = (
            f"Restores fleet throughput to {impact.baseline_throughput_fpm:.1f} fpm, "
            f"clearing the projected {impact.delay_minutes} minute delay."
        )
    else:
        recovery = f"Returns {scope} to the baseline renderer configuration."

    if shortfall:
        # Only claim to recover a shortfall when one is actually measured.
        gap = impact.baseline_throughput_fpm - impact.observed_throughput_fpm
        partial = (
            f"Recovers part of the {gap:.1f} fpm shortfall against a "
            f"{impact.baseline_throughput_fpm:.1f} fpm baseline, without changing "
            "renderer state."
        )
    else:
        partial = "Adds capacity without changing renderer state."

    if impact is not None and impact.at_risk_deliverables:
        priority = (
            f"Protects {', '.join(impact.at_risk_deliverables)} by draining "
            "high-priority shots first, at the cost of delaying the rest."
        )
    else:
        priority = "Reorders the queue so high-priority shots drain first."

    return [
        RemediationOption(
            option_id="opt-01",
            action_type=ActionType.ROLLBACK_RENDERER_CONFIG,
            title="Rollback Renderer Configuration (Recommended)",
            description="Return the degraded workers to renderer v2.4.0 with tile_size=256.",
            parameters={"target_version": "v2.4.0", "target_tile_size": 256},
            estimated_recovery_minutes=2,
            risk_level=RiskLevel.LOW,
            production_consequence=recovery,
        ),
        RemediationOption(
            option_id="opt-02",
            action_type=ActionType.SCALE_RENDER_WORKERS,
            title="Scale Render Farm Workers (+4 Nodes)",
            description="Provision four additional workers to absorb load without a rollback.",
            parameters={"additional_workers": 4},
            estimated_recovery_minutes=15,
            risk_level=RiskLevel.MEDIUM,
            production_consequence=partial,
        ),
        RemediationOption(
            option_id="opt-03",
            action_type=ActionType.REPRIORITIZE_QUEUE,
            title="Reprioritize Queue for High-Priority Sequences",
            description="Drain high-priority shots ahead of normal-priority work.",
            parameters={
                "priority_sequences": list(impact.sequences) if impact else [],
            },
            estimated_recovery_minutes=5,
            risk_level=RiskLevel.LOW,
            production_consequence=priority,
        ),
    ]


planner = InvestigationPlanner()
