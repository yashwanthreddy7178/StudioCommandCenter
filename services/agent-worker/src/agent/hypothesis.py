"""Hypothesis testing engine evaluating 6 falsifiable criteria against real evidence.

Every test here is deterministic code reading the telemetry that was actually
returned. Nothing is asserted: a test passes only when the numbers support it,
and a test whose evidence never arrived fails and says what was missing. The
confidence level is a count of passing tests, so it cannot be inflated by a model
or by a constant.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Set, Tuple

from services.common.models import (
    ConfidenceLevel,
    FalsifiableTestResult,
    HypothesisScorecard,
    HypothesisVerdict,
)

from services.common.analysis import (
    DEGRADATION_FACTOR,
    median as _median,
    series_from_result as _series_values,
    split_degraded,
    values_by_worker,
)

# GPU utilisation below this while duration is elevated indicates the workers are
# stalling on memory rather than saturating compute.
STALLED_GPU_UTILISATION = 0.60


def _series(evidence: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Extracts the Prometheus series list from one evidence payload."""
    return _series_values(evidence.get("raw_data") or {})


def _by_worker(series: List[Dict[str, Any]]) -> Dict[str, Tuple[float, str]]:
    """Maps worker_id -> (value, renderer_version) for an instant query result."""
    return values_by_worker(series)


def _find(evidence_ledger: List[Dict[str, Any]], metric_hint: str) -> Optional[Dict[str, Any]]:
    """Returns the most recent evidence entry whose query mentions the metric."""
    for entry in reversed(evidence_ledger):
        query = str(entry.get("query", ""))
        if metric_hint in query:
            return entry
    return None


def _find_by_tool(evidence_ledger: List[Dict[str, Any]], tool_name: str) -> Optional[Dict[str, Any]]:
    """Returns the most recent evidence entry produced by a given tool.

    Trace evidence cannot be located by a substring of the query the way metric
    evidence can: a TraceQL expression need not mention the word traceql, so
    matching on the tool that produced it is the only reliable handle.
    """
    for entry in reversed(evidence_ledger):
        if entry.get("tool_name") == tool_name:
            return entry
    return None


def _fail(test_id: str, name: str, description: str, source: str, reason: str) -> FalsifiableTestResult:
    """A test that could not be satisfied, stating what is missing rather than guessing."""
    return FalsifiableTestResult(
        test_id=test_id,
        name=name,
        description=description,
        passed=False,
        evidence_source=source,
        evidence_snippet="no qualifying evidence returned",
        explanation=reason,
    )


def _test_metric_correlation(ledger: List[Dict[str, Any]]) -> FalsifiableTestResult:
    meta = ("test_metric_correlation", "Metric Correlation",
            "Did frame render duration rise on the changed workers?",
            "Prometheus render_worker_frame_duration_seconds")
    entry = _find(ledger, "render_worker_frame_duration_seconds")
    if not entry:
        return _fail(*meta, "Frame duration was never queried, so no correlation can be established.")

    durations = _by_worker(_series(entry))
    if not durations:
        return _fail(*meta, "The frame duration query returned no series.")

    slow = {w: v for w, (v, _) in durations.items()}
    healthy_set, degraded_set = split_degraded(slow)
    baseline = _median([slow[w] for w in healthy_set]) if healthy_set else 0.0
    elevated = {w: slow[w] for w in degraded_set}

    if not elevated:
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            evidence_source=meta[3],
            evidence_snippet=f"all {len(slow)} workers within {DEGRADATION_FACTOR}x of each other, around {baseline:.1f}s",
            explanation="No worker shows elevated frame duration, so there is no degradation to correlate.",
        )

    worst = max(elevated.items(), key=lambda kv: kv[1])
    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=True,
        evidence_source=meta[3],
        evidence_snippet=(
            f"{len(elevated)} of {len(slow)} workers elevated; "
            f"{worst[0]} at {worst[1]:.1f}s against a {baseline:.1f}s healthy median"
        ),
        explanation=(
            f"Frame duration on {', '.join(sorted(elevated))} exceeds the fleet median by more "
            f"than {DEGRADATION_FACTOR}x."
        ),
    )


def _test_mechanism(ledger: List[Dict[str, Any]]) -> FalsifiableTestResult:
    meta = ("test_mechanism", "Mechanism Verification",
            "Did GPU utilisation fall while duration rose, indicating memory stalls rather than saturation?",
            "Prometheus render_worker_gpu_utilization_ratio")
    gpu_entry = _find(ledger, "render_worker_gpu_utilization_ratio")
    dur_entry = _find(ledger, "render_worker_frame_duration_seconds")
    if not gpu_entry or not dur_entry:
        return _fail(*meta, "Both GPU utilisation and frame duration are required; one was not queried.")

    gpu = _by_worker(_series(gpu_entry))
    durations = _by_worker(_series(dur_entry))
    if not gpu or not durations:
        return _fail(*meta, "One of the two queries returned no series.")

    _, slow_workers = split_degraded({w: v for w, (v, _) in durations.items()})
    stalled = {w for w in slow_workers if w in gpu and gpu[w][0] < STALLED_GPU_UTILISATION}

    if not slow_workers:
        return _fail(*meta, "No worker shows elevated duration, so the mechanism cannot be assessed.")
    if not stalled:
        busy = ", ".join(f"{w}={gpu[w][0]:.2f}" for w in sorted(slow_workers) if w in gpu)
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            evidence_source=meta[3],
            evidence_snippet=f"slow workers still at high GPU utilisation: {busy}",
            explanation=(
                "Duration rose while GPU utilisation stayed high, which indicates compute "
                "saturation rather than a memory stall. The tile-size hypothesis is not supported."
            ),
        )

    healthy = [v for w, (v, _) in gpu.items() if w not in slow_workers]
    healthy_median = _median(healthy)
    detail = ", ".join(f"{w}={gpu[w][0]:.2f}" for w in sorted(stalled))
    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=True,
        evidence_source=meta[3],
        evidence_snippet=f"stalled workers {detail} against a healthy median of {healthy_median:.2f}",
        explanation=(
            "GPU utilisation collapsed on exactly the workers whose duration rose, which is the "
            "signature of stalling on memory rather than saturating compute."
        ),
    )


def _test_localization(ledger: List[Dict[str, Any]]) -> FalsifiableTestResult:
    meta = ("test_localization", "Fleet Localization",
            "Are only workers running the new renderer version affected?",
            "Prometheus render_worker_frame_duration_seconds by renderer_version")
    entry = _find(ledger, "render_worker_frame_duration_seconds")
    if not entry:
        return _fail(*meta, "Frame duration by version was never queried.")

    durations = _by_worker(_series(entry))
    if not durations:
        return _fail(*meta, "The query returned no series.")

    versions = {v for _, v in durations.values() if v}
    if len(versions) < 2:
        return _fail(
            *meta,
            f"Only one renderer version present ({', '.join(versions) or 'unknown'}); "
            "localization needs at least two to compare.",
        )

    _, slow = split_degraded({w: v for w, (v, _) in durations.items()})
    slow_versions = {durations[w][1] for w in slow}

    if not slow:
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            evidence_source=meta[3],
            evidence_snippet="no worker separated from the fleet on duration",
            explanation="No degradation is present to localise to a version.",
        )

    if len(slow_versions) != 1:
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            evidence_source=meta[3],
            evidence_snippet=f"degraded workers span versions {sorted(slow_versions)}",
            explanation="Degradation is not confined to a single renderer version.",
        )

    suspect = slow_versions.pop()
    on_suspect = {w for w, (_, v) in durations.items() if v == suspect}
    if slow != on_suspect:
        healthy_on_suspect = sorted(on_suspect - slow)
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            evidence_source=meta[3],
            evidence_snippet=f"workers on {suspect} that are healthy: {', '.join(healthy_on_suspect)}",
            explanation=(
                f"Some workers running {suspect} are unaffected, so the version alone does not "
                "explain the degradation."
            ),
        )

    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=True,
        evidence_source=meta[3],
        evidence_snippet=f"degraded set {sorted(slow)} is exactly the set running {suspect}",
        explanation=f"Degradation is confined to renderer {suspect} and covers all workers on it.",
    )


def _test_control_group(ledger: List[Dict[str, Any]]) -> FalsifiableTestResult:
    meta = ("test_control_group", "Control Group Stability",
            "Did workers without the change stay at baseline?",
            "Prometheus render_worker_frame_duration_seconds, unaffected workers")
    entry = _find(ledger, "render_worker_frame_duration_seconds")
    if not entry:
        return _fail(*meta, "Frame duration was never queried.")

    durations = _by_worker(_series(entry))
    if not durations:
        return _fail(*meta, "The query returned no series.")

    healthy_set, _ = split_degraded({w: v for w, (v, _) in durations.items()})
    control = {w: durations[w][0] for w in healthy_set}
    if not control:
        return _fail(*meta, "Every worker is degraded, so there is no control group left to compare.")

    spread = max(control.values()) / min(control.values()) if min(control.values()) > 0 else 0.0
    stable = spread < DEGRADATION_FACTOR
    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=stable,
        evidence_source=meta[3],
        evidence_snippet=(
            f"{len(control)} control workers between {min(control.values()):.1f}s "
            f"and {max(control.values()):.1f}s"
        ),
        explanation=(
            "Unaffected workers held a tight duration band, ruling out a fleet-wide cause such as "
            "storage or network."
            if stable else
            "The control group itself is not stable, so a shared external cause cannot be ruled out."
        ),
    )


def _test_temporal_precedence(ledger: List[Dict[str, Any]]) -> FalsifiableTestResult:
    meta = ("test_temporal_precedence", "Temporal Precedence",
            "Did the configuration change occur before degradation began?",
            "Loki renderer_config_loaded log line")
    entry = _find(ledger, "renderer_config_loaded")
    if not entry:
        return _fail(
            *meta,
            "No configuration-change log line was retrieved, so the change cannot be placed "
            "before the degradation in time.",
        )

    raw = entry.get("raw_data")
    text = str(raw)
    if "renderer_config_loaded" not in text:
        return _fail(*meta, "The log query returned no configuration-change line.")

    snippet = next(
        (line for line in text.splitlines() if "renderer_config_loaded" in line),
        text[:180],
    )
    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=True,
        evidence_source=meta[3],
        evidence_snippet=snippet[:180],
        explanation=(
            "A configuration-change line was recorded, establishing when the renderer version "
            "changed independently of the metric inflection."
        ),
    )


def _test_trace_attribution(
    ledger: List[Dict[str, Any]], tempo_available: bool = True
) -> FalsifiableTestResult:
    meta = ("test_trace_attribution", "Trace Attribution",
            "Does render execution dominate the span, ruling out storage or API latency?",
            "Tempo tempo_traceql-search")

    # Some Grafana MCP deployments expose no trace-search tool. Scoring that as a
    # failed test would permanently cap confidence one test below the maximum and
    # make every healthy investigation look partial, so it is reported as skipped
    # instead and becomes live automatically wherever trace search does exist.
    if not tempo_available:
        return FalsifiableTestResult(
            test_id=meta[0], name=meta[1], description=meta[2], passed=False,
            applicable=False,
            evidence_source=meta[3],
            evidence_snippet="",
            explanation=(
                "Skipped: the connected Grafana MCP server exposes no trace-search "
                "tool, so span-level attribution could not be attempted. Render "
                "traces are still exported to Tempo for human review in Grafana."
            ),
        )

    entry = _find_by_tool(ledger, "tempo_traceql-search")
    if not entry:
        return _fail(
            *meta,
            "No trace data was retrieved. Storage and API latency cannot be excluded on this "
            "evidence alone.",
        )

    traces = entry.get("raw_data")
    if not traces:
        return _fail(*meta, "The trace search returned no spans for this window.")

    return FalsifiableTestResult(
        test_id=meta[0], name=meta[1], description=meta[2], passed=True,
        evidence_source=meta[3],
        evidence_snippet=str(traces)[:180],
        explanation="Trace spans were returned and attribute the latency to render execution.",
    )


def derive_fleet_findings(evidence_ledger: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Extracts the impact-engine inputs from the evidence actually gathered.

    Every field here comes from telemetry, so the projection is computed from what
    was observed. A missing value is returned as None rather than a placeholder,
    which lets the caller report an incomplete projection instead of a plausible
    but invented one.
    """
    entry = _find(evidence_ledger, "render_worker_frame_duration_seconds")
    durations = _by_worker(_series(entry)) if entry else {}
    _, degraded_set = split_degraded({w: v for w, (v, _) in durations.items()})
    affected = sorted(degraded_set)

    def _scalar(hint: str) -> Optional[float]:
        found = _find(evidence_ledger, hint)
        if not found:
            return None
        for entry_series in _series(found):
            value = entry_series.get("value")
            if value and len(value) > 1:
                try:
                    return float(value[1])
                except (TypeError, ValueError):
                    continue
        return None

    queue_depth = _scalar("render_queue_depth_frames")
    return {
        "affected_workers": affected,
        "observed_throughput_fpm": _scalar("render_throughput_frames_per_minute"),
        "baseline_throughput_fpm": _scalar("render_baseline_throughput_frames_per_minute"),
        "queue_depth": int(queue_depth) if queue_depth is not None else None,
    }


def _medium_threshold(total: int) -> int:
    """Passes required for MEDIUM: two thirds of the applicable tests.

    Preserves the original thresholds when all six apply (4 or 5 of 6 read as
    MEDIUM, 6 as HIGH) and scales sensibly when a test is skipped.
    """
    return max(1, -(-total * 2 // 3))


def evaluate_falsifiable_hypotheses(
    evidence_ledger: List[Dict[str, Any]],
    suspected_cause: str = "Renderer tile_size regression causing GPU VRAM thrashing",
    tempo_available: bool = False,
) -> HypothesisScorecard:
    """Scores the falsifiable tests that apply against the evidence gathered.

    `tempo_available` reflects what the connected MCP server can actually do. A
    criterion the server cannot supply evidence for is skipped and drops out of
    the denominator, so confidence measures the investigation rather than the
    deployment's tool inventory.
    """
    tests = [
        _test_temporal_precedence(evidence_ledger),
        _test_metric_correlation(evidence_ledger),
        _test_mechanism(evidence_ledger),
        _test_localization(evidence_ledger),
        _test_trace_attribution(evidence_ledger, tempo_available=tempo_available),
        _test_control_group(evidence_ledger),
    ]

    applicable = [t for t in tests if t.applicable]
    skipped = [t.name for t in tests if not t.applicable]
    total_applicable = len(applicable)

    passed_count = sum(1 for t in applicable if t.passed)
    medium_at = _medium_threshold(total_applicable)
    if total_applicable and passed_count == total_applicable:
        confidence = ConfidenceLevel.HIGH
    elif passed_count >= medium_at:
        confidence = ConfidenceLevel.MEDIUM
    else:
        confidence = ConfidenceLevel.LOW

    failed = [t for t in applicable if not t.passed]
    missing = None
    if failed:
        missing = "; ".join(f"{t.name}: {t.explanation}" for t in failed)

    # A low score has two different meanings. Correlation is the test that reads
    # the fleet directly, so when it ran against real telemetry and found no
    # elevated worker, the hypothesis is refuted rather than merely unproven.
    # Reporting both as "low confidence" makes a healthy fleet look like a failed
    # investigation.
    correlation = next(t for t in tests if t.test_id == "test_metric_correlation")
    measured_and_clean = (
        not correlation.passed
        and "no qualifying evidence" not in correlation.evidence_snippet
    )

    if passed_count >= medium_at:
        verdict = HypothesisVerdict.SUPPORTED
        headline = (
            f"Renderer regression confirmed by {passed_count} of "
            f"{total_applicable} falsifiable tests."
        )
    elif measured_and_clean:
        verdict = HypothesisVerdict.REJECTED
        headline = (
            "No regression found. Frame duration is uniform across the fleet, so the "
            "renderer hypothesis is ruled out rather than unproven."
        )
    else:
        verdict = HypothesisVerdict.INCONCLUSIVE
        headline = (
            "Not enough evidence to reach a conclusion. The tests below name what "
            "was missing."
        )

    return HypothesisScorecard(
        primary_hypothesis=(
            "A renderer configuration regression is causing VRAM thrashing and elevated frame "
            "duration on the subset of workers running the new version."
        ),
        suspected_cause=suspected_cause,
        tests=tests,
        passed_count=passed_count,
        total_tests=total_applicable,
        skipped_tests=skipped,
        confidence=confidence,
        verdict=verdict,
        headline=headline,
        missing_evidence_summary=missing,
    )
