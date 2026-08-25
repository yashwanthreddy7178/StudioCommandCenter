"""Tool client invoking Grafana MCP tools via mcp-gateway and impact calculations."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import httpx
from src.config import settings
from services.common.models import ImpactProjection, RemediationOption
from services.common.telemetry import setup_logging

logger = setup_logging("agent-worker-tools")


class AgentToolClient:
    """Client for calling allowlisted MCP tools through mcp-gateway and deterministic services."""

    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=15.0)

    async def call_mcp_gateway(
        self,
        tool_name: str,
        parameters: Dict[str, Any],
        tenant_id: str,
        run_id: str,
    ) -> Dict[str, Any]:
        """Invokes Grafana MCP tool through the caching mcp-gateway."""
        url = f"{settings.mcp_gateway_url}/call"
        payload = {
            "tool_name": tool_name,
            "parameters": parameters,
            "tenant_id": tenant_id,
            "run_id": run_id,
        }
        res = await self._http_client.post(url, json=payload)
        res.raise_for_status()
        return res.json()

    async def calculate_impact(
        self,
        tenant_id: str,
        affected_workers: List[str],
        observed_throughput_fpm: float,
        baseline_throughput_fpm: float,
        queue_depth: int,
    ) -> ImpactProjection:
        """Calls impact-engine for deterministic delivery projections."""
        url = f"{settings.impact_engine_url}/impact/project"
        payload = {
            "tenant_id": tenant_id,
            "affected_workers": affected_workers,
            "observed_throughput_fpm": observed_throughput_fpm,
            "baseline_throughput_fpm": baseline_throughput_fpm,
            "queue_depth": queue_depth,
        }
        res = await self._http_client.post(url, json=payload)
        res.raise_for_status()
        return ImpactProjection.model_validate(res.json())

    async def close(self) -> None:
        await self._http_client.aclose()


tool_client = AgentToolClient()
