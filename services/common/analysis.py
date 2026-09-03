"""Shared telemetry analysis primitives.

Lives in `common` because both the investigation and the post-remediation
verification have to decide which workers are degraded, and they must decide it
the same way. A verifier using different logic from the investigator could report
recovery that the investigator would not recognise.
"""
from __future__ import annotations

from typing import Any, Dict, List, Set, Tuple

# Minimum multiplicative gap between the healthy and degraded groups before a
# split is accepted. Wide enough that ordinary jitter, and the spread between
# fast and slow GPU models in a mixed fleet, never trips it.
DEGRADATION_FACTOR = 2.0


def median(values: List[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def split_degraded(durations: Dict[str, float]) -> Tuple[Set[str], Set[str]]:
    """Partitions workers into (healthy, degraded) by the largest gap in duration.

    Comparing each worker against the fleet median only works while degraded
    workers are a small minority: once they approach half the fleet the median
    sits between the two groups and nothing clears the threshold, so a severe
    incident reads as no incident at all.

    Sorting the durations and cutting at the largest multiplicative gap has no
    such assumption. It separates any proportion of degraded workers, and on a
    healthy fleet the largest gap is the ordinary spread between GPU models,
    which falls below the factor and yields no split.
    """
    if len(durations) < 2:
        return set(durations), set()

    ordered = sorted(durations.items(), key=lambda kv: kv[1])
    best_ratio, best_index = 1.0, None
    for i in range(len(ordered) - 1):
        lower, upper = ordered[i][1], ordered[i + 1][1]
        if lower <= 0:
            continue
        ratio = upper / lower
        if ratio > best_ratio:
            best_ratio, best_index = ratio, i

    if best_index is None or best_ratio < DEGRADATION_FACTOR:
        return set(durations), set()

    healthy = {name for name, _ in ordered[: best_index + 1]}
    degraded = {name for name, _ in ordered[best_index + 1:]}
    return healthy, degraded


def series_from_result(result: Any) -> List[Dict[str, Any]]:
    """Extracts the Prometheus series list from an MCP tool result."""
    if isinstance(result, dict):
        data = result.get("data", result)
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict)]
    if isinstance(result, list):
        return [s for s in result if isinstance(s, dict)]
    return []


def values_by_worker(series: List[Dict[str, Any]]) -> Dict[str, Tuple[float, str]]:
    """Maps worker_id -> (value, renderer_version), keeping the freshest sample.

    A worker can appear more than once in one instant query. `renderer_version` is
    a label, so changing it forks the worker into a new series while the old one
    stays queryable until its last sample falls outside the lookback window; a
    process restart does the same through `instance`. Taking whichever row happens
    to come last would then read a rolled-back worker as still degraded, which is
    precisely the moment the answer has to be right. Comparing sample timestamps
    resolves it: the series still being written is the current one.
    """
    newest: Dict[str, float] = {}
    out: Dict[str, Tuple[float, str]] = {}
    for entry in series:
        metric = entry.get("metric", {})
        worker = metric.get("worker_id")
        value = entry.get("value")
        if not worker or not value or len(value) < 2:
            continue
        try:
            timestamp = float(value[0])
            reading = float(value[1])
        except (TypeError, ValueError):
            continue
        if worker in newest and timestamp <= newest[worker]:
            continue
        newest[worker] = timestamp
        out[worker] = (reading, metric.get("renderer_version", ""))
    return out


def scalar_from_result(result: Any) -> float | None:
    """Returns the first numeric sample from an instant query result."""
    for entry in series_from_result(result):
        value = entry.get("value")
        if value and len(value) > 1:
            try:
                return float(value[1])
            except (TypeError, ValueError):
                continue
    return None
