"""Post-remediation verification.

Re-queries Grafana after a settle window and compares the result against the
state recorded before the action. Recovery is a conclusion drawn from the new
telemetry, never an assumption: an action that failed, partially worked, or made
things worse produces a verification that says so.
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional

import httpx

from src.config import settings
from services.common.analysis import (
    scalar_from_result,
    series_from_result,
    split_degraded,
    values_by_worker,
)
from services.common.telemetry import setup_logging

logger = setup_logging("action-executor-verifier")


class RemediationVerifier:
    """Checks whether an applied remediation actually restored the fleet."""

    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=30.0)

    async def _query(self, tenant_id: str, run_id: str, expr: str) -> Any:
        """Runs one PromQL query through mcp-gateway, returning the raw result."""
        response = await self._http_client.post(
            f"{settings.mcp_gateway_url}/call",
            json={
                "tool_name": "query_prometheus",
                "parameters": {"expr": expr, "queryType": "instant", "endTime": "now"},
                "tenant_id": tenant_id,
                "run_id": run_id,
            },
        )
        response.raise_for_status()
        return response.json().get("result")

    async def verify_recovery(
        self,
        tenant_id: str,
        run_id: str,
        delay_minutes_before: Optional[int] = None,
        observed_fpm_before: Optional[float] = None,
        settle_seconds: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Settles, re-queries telemetry, and reports whether the fleet recovered."""
        settle = (
            settle_seconds
            if settle_seconds is not None
            else settings.verification_settle_sec
        )
        logger.info(
            "Starting verification settle window",
            extra={"tenant_id": tenant_id, "settle_sec": settle},
        )
        await asyncio.sleep(settle)

        try:
            durations_raw = await self._query(
                tenant_id, run_id, "render_worker_frame_duration_seconds"
            )
            throughput = await self._query(
                tenant_id, run_id, "render_throughput_frames_per_minute"
            )
            baseline = await self._query(
                tenant_id, run_id, "render_baseline_throughput_frames_per_minute"
            )
            queue = await self._query(tenant_id, run_id, "render_queue_depth_frames")
        except Exception as exc:
            # Without post-action telemetry there is nothing to conclude from.
            logger.error("Verification telemetry unavailable", extra={"error": str(exc)})
            return {
                "status": "UNVERIFIED",
                "is_recovered": False,
                "reason": f"Post-action telemetry could not be read: {str(exc)[:200]}",
            }

        durations = values_by_worker(series_from_result(durations_raw))
        if not durations:
            return {
                "status": "UNVERIFIED",
                "is_recovered": False,
                "reason": "No worker telemetry was returned after the settle window.",
            }

        _, still_degraded = split_degraded({w: v for w, (v, _) in durations.items()})
        observed_fpm = scalar_from_result(throughput)
        baseline_fpm = scalar_from_result(baseline)
        queue_depth = scalar_from_result(queue)

        recomputed: Dict[str, Any] = {}
        if None not in (observed_fpm, baseline_fpm, queue_depth):
            try:
                impact_res = await self._http_client.post(
                    f"{settings.impact_engine_url}/impact/project",
                    json={
                        "tenant_id": tenant_id,
                        "affected_workers": sorted(still_degraded),
                        "observed_throughput_fpm": observed_fpm,
                        "baseline_throughput_fpm": baseline_fpm,
                        "queue_depth": int(queue_depth),
                    },
                )
                if impact_res.status_code == 200:
                    recomputed = impact_res.json()
            except Exception as exc:
                logger.warning(
                    "Impact recomputation failed during verification",
                    extra={"error": str(exc)},
                )

        delay_after = recomputed.get("delay_minutes")
        recovered = not still_degraded
        if recovered and delay_minutes_before is not None and delay_after is not None:
            # Workers healthy but the deadline still missed is a partial result,
            # not a success. The backlog accumulated during the incident may not
            # be recoverable by fixing the workers alone.
            recovered = delay_after <= 0

        if still_degraded:
            status = "NOT_RECOVERED"
            reason = (
                f"{len(still_degraded)} worker(s) remain degraded after the settle "
                f"window: {', '.join(sorted(still_degraded))}."
            )
        elif delay_after is not None and delay_after > 0:
            status = "PARTIALLY_RECOVERED"
            reason = (
                "All workers returned to baseline, but the accumulated backlog "
                f"still projects a {delay_after} minute delay."
            )
        else:
            status = "VERIFIED"
            reason = "All workers returned to baseline and the deadline is projected to be met."

        improvement = None
        if delay_minutes_before is not None and delay_after is not None:
            improvement = delay_minutes_before - delay_after

        logger.info(
            "Verification completed",
            extra={
                "tenant_id": tenant_id,
                "status": status,
                "still_degraded": len(still_degraded),
                "delay_after": delay_after,
            },
        )

        return {
            "status": status,
            "is_recovered": recovered,
            "reason": reason,
            "still_degraded_workers": sorted(still_degraded),
            "observed_throughput_fpm": observed_fpm,
            "baseline_throughput_fpm": baseline_fpm,
            "queue_depth": queue_depth,
            "delay_minutes_before": delay_minutes_before,
            "delay_minutes_after": delay_after,
            "delay_minutes_recovered": improvement,
            "verification_impact": recomputed,
        }

    async def close(self) -> None:
        await self._http_client.aclose()


verifier = RemediationVerifier()
