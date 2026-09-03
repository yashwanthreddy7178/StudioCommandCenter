"""Instruction text and result shaping for the investigation agent.

Tool declarations are not written here: ADK derives them from the signature and
docstring of each tool function in `planner`, so the schema cannot drift from the
implementation.
"""
from __future__ import annotations

from typing import Any, List

# Metric names the simulator exports, given to the agent so it queries real
# series rather than guessing at names.
AVAILABLE_METRICS = [
    "render_worker_frame_duration_seconds",
    "render_worker_gpu_utilization_ratio",
    "render_worker_gpu_memory_used_bytes",
    "render_worker_temperature_celsius",
    "render_worker_cpu_utilization_ratio",
    "render_worker_memory_used_bytes",
    "render_worker_active_jobs",
    "render_queue_depth_frames",
    "render_throughput_frames_per_minute",
    "render_baseline_throughput_frames_per_minute",
    "render_fleet_total_workers",
    "render_fleet_degraded_workers",
]

# Labels carried by the worker series, needed for localization and control-group
# comparisons.
AVAILABLE_LABELS = ["tenant_id", "worker_id", "renderer_version", "gpu_type"]


PLANNING_INSTRUCTION = """Investigate this objective:

{objective}

Available metrics:
""" + "\n".join(f"  {m}" for m in AVAILABLE_METRICS) + """

Labels on the per-worker series: """ + ", ".join(AVAILABLE_LABELS) + """

Establish, as far as the telemetry allows:
- whether frame duration has risen on any workers, and by how much
- whether GPU utilisation fell on those same workers, which indicates a memory
  stall, or stayed high, which indicates compute saturation
- whether the affected workers share a renderer_version that healthy ones do not
- whether workers on the other version stayed healthy
- when the renderer configuration last changed, from the logs
- current and baseline fleet throughput, and queue depth

Issue one query at a time and let each result inform the next. Do not assume a
result you have not queried. Stop once the evidence is sufficient, or once
further queries would add nothing, and summarise what the evidence shows.

You do not draw the final conclusion and you do not compute any number that
reaches the user: the falsifiable tests and the delivery impact are evaluated in
code from the evidence you collect. Your task is to collect the right evidence.
"""


# Handing the agent its metric names makes a run cheaper and more predictable, but
# it also does the discovery for it: a fleet whose series it was told about is not
# a fleet it explored. The discovery wording names only the namespace and expects
# the agent to enumerate what the farm actually publishes, which costs a turn and
# is what an investigator would really do.
_METRIC_DISCOVERY = """You have not been told which metrics exist. Call
list_metric_names first and work from what the farm actually publishes. The
render farm's series are prefixed `render_`; ignore anything else the datasource
carries."""


def _metric_inventory_block() -> str:
    """The inventory wording exactly as PLANNING_INSTRUCTION embeds it.

    Rebuilt from the same constant the instruction is built from, so the two
    cannot drift apart and leave the swap below silently matching nothing.
    """
    return "Available metrics:\n" + "\n".join(f"  {m}" for m in AVAILABLE_METRICS)


def planning_instruction(objective: str, discover_metrics: bool = False) -> str:
    """Builds the opening instruction for one investigation."""
    template = PLANNING_INSTRUCTION
    if discover_metrics:
        inventory = _metric_inventory_block()
        if inventory not in template:  # pragma: no cover - guarded by the test below
            raise RuntimeError(
                "Metric inventory block not found in PLANNING_INSTRUCTION; the "
                "discovery variant would silently fall back to the fixed list."
            )
        template = template.replace(inventory, _METRIC_DISCOVERY)
    return template.format(objective=objective)


def summarise_for_model(result: Any, max_series: int = 12) -> str:
    """Renders a tool result compactly enough to feed back into the context.

    Prometheus payloads collapse to worker/version/value triples and log payloads
    to their lines, which keeps a fourteen-turn investigation inside the per-run
    token budget while preserving everything the next decision depends on. Raw
    payloads stay in the evidence ledger and are referenced by id.
    """
    if not isinstance(result, dict):
        return str(result)[:1500]

    rows = result.get("data")
    if not isinstance(rows, list) or not rows:
        return "no data returned"

    lines: List[str] = []
    for row in rows[:max_series]:
        if not isinstance(row, dict):
            continue
        if "line" in row:
            lines.append(str(row["line"])[:200])
            continue
        metric = row.get("metric", {})
        value = row.get("value") or []
        label = metric.get("worker_id") or metric.get("tenant_id") or "series"
        version = metric.get("renderer_version")
        reading = value[1] if len(value) > 1 else "?"
        lines.append(f"{label}{f' [{version}]' if version else ''} = {reading}")

    if len(rows) > max_series:
        lines.append(f"... and {len(rows) - max_series} more series")
    return "\n".join(lines) if lines else "no data returned"
