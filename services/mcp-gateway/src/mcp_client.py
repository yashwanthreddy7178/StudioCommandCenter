"""Grafana MCP Upstream Client with live MCP connection and local simulator fallback."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
import httpx
from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway-client")


class GrafanaMCPClient:
    """Executes verified Grafana MCP queries against Grafana Cloud or local simulator."""

    def __init__(self) -> None:
        self._http_client = httpx.AsyncClient(timeout=10.0)

    async def call_upstream_mcp(self, tool_name: str, parameters: Dict[str, Any], tenant_id: str) -> Any:
        """Invokes the tool on the upstream Grafana MCP server or local simulator fallback."""
        # 1. If live Grafana MCP endpoint is configured, make real MCP call
        if settings.grafana_mcp_server_url and settings.grafana_service_account_token:
            return await self._call_live_grafana_mcp(tool_name, parameters)

        # 2. Fallback to querying render-sim telemetry for offline / local testing
        return await self._call_local_render_sim_mcp(tool_name, parameters, tenant_id)

    async def _call_live_grafana_mcp(self, tool_name: str, parameters: Dict[str, Any]) -> Any:
        """Executes a JSON-RPC / MCP call to the live Grafana MCP server."""
        headers = {
            "Authorization": f"Bearer {settings.grafana_service_account_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "name": tool_name,
                "arguments": parameters,
            },
            "id": 1,
        }
        response = await self._http_client.post(
            f"{settings.grafana_mcp_server_url}/rpc",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise RuntimeError(f"Grafana MCP upstream error: {data['error']}")
        return data.get("result", {})

    async def _call_local_render_sim_mcp(self, tool_name: str, parameters: Dict[str, Any], tenant_id: str) -> Any:
        """Emulates Grafana MCP tool responses using render-sim telemetry."""
        # Query render-sim telemetry endpoint; gracefully degrade when not running (e.g. tests)
        world_data: Dict[str, Any] = {}
        try:
            res = await self._http_client.get(
                f"{settings.render_sim_url}/worlds/{tenant_id}", timeout=2.0
            )
            if res.status_code == 200:
                world_data = res.json()
        except Exception:
            # render-sim not available (unit test environment) — use static stubs
            world_data = {}

        workers = world_data.get("workers", [])

        if tool_name == "list_prometheus_metric_names":
            return {
                "metrics": [
                    "render_worker_frame_duration_seconds",
                    "render_worker_gpu_utilization_ratio",
                    "render_worker_gpu_memory_used_bytes",
                    "render_worker_temperature_celsius",
                    "render_worker_cpu_utilization_ratio",
                    "render_worker_memory_used_bytes",
                    "render_queue_depth_frames",
                    "render_throughput_frames_per_minute",
                ]
            }

        elif tool_name == "list_prometheus_label_values":
            label = parameters.get("label_name", "worker_id")
            if label == "worker_id":
                return {"values": [w.get("worker_id") for w in workers]}
            elif label == "renderer_version":
                return {"values": ["v2.4.0", "v2.4.1"]}
            return {"values": ["t01", "t02", "t07", "t24"]}

        elif tool_name in {"query_prometheus", "query_prometheus_histogram"}:
            query = parameters.get("query", "")
            # Return realistic Prometheus vector/matrix format
            result_series = []
            for w in workers:
                wid = w.get("worker_id")
                gpu_util = w.get("gpu_utilization_pct", 94.0) / 100.0
                duration = w.get("current_frame_duration_sec", 22.0)
                version = w.get("renderer_version", "v2.4.0")

                if "duration" in query:
                    val = duration
                elif "gpu_utilization" in query:
                    val = gpu_util
                elif "throughput" in query:
                    val = world_data.get("observed_throughput_fpm", 118.6)
                elif "queue" in query:
                    val = world_data.get("queue_depth", 18432)
                else:
                    val = 1.0

                result_series.append({
                    "metric": {
                        "__name__": "render_metric",
                        "tenant_id": tenant_id,
                        "worker_id": wid,
                        "renderer_version": version,
                        "gpu_type": w.get("gpu_type", "NVIDIA RTX 4090"),
                    },
                    "value": [1725465600, str(val)],
                })

            return {
                "resultType": "vector",
                "result": result_series,
            }

        elif tool_name in {"query_loki_logs", "query_loki_stats"}:
            # Return realistic Loki streams
            entries = []
            for w in workers:
                wid = w.get("worker_id")
                is_degraded = w.get("is_degraded", False)
                ver = w.get("renderer_version", "v2.4.0")
                if is_degraded:
                    line = f"[WARN] Worker {wid} [v{ver}] tile_size=2048 VRAM paging stall, duration={w.get('current_frame_duration_sec', 145.0):.1f}s"
                else:
                    line = f"[INFO] Worker {wid} [v{ver}] completed frame in {w.get('current_frame_duration_sec', 22.0):.1f}s"
                entries.append([str(int(1725465600 * 1e9)), line])

            return {
                "resultType": "streams",
                "result": [
                    {
                        "stream": {"tenant_id": tenant_id, "job": "render"},
                        "values": entries,
                    }
                ],
            }

        elif tool_name == "search_tempo_traces":
            return {
                "traces": [
                    {
                        "traceID": "4bf92f3577b34da6a3ce929d0e0e4736",
                        "rootServiceName": "render-pipeline",
                        "rootTraceName": "render_frame_cycles",
                        "durationMs": 145000 if world_data.get("is_incident_active") else 22000,
                        "spanCount": 14,
                    }
                ]
            }

        elif tool_name in {"list_alert_rules", "get_alert_rule_by_uid"}:
            return {
                "rules": [
                    {
                        "uid": "alert-vfx-render-delay",
                        "title": "Render Fleet Throughput Degradation",
                        "state": "firing" if world_data.get("is_incident_active") else "normal",
                        "labels": {"tenant_id": tenant_id, "severity": "critical"},
                    }
                ]
            }

        elif tool_name == "list_incidents":
            return {
                "incidents": [
                    {
                        "id": "inc-0842",
                        "title": "Render throughput dropped 65% on v2.4.1 workers",
                        "status": "active" if world_data.get("is_incident_active") else "resolved",
                        "created_at": "2026-09-04T14:50:00Z",
                    }
                ]
            }

        return {"data": "ok"}

    async def close(self) -> None:
        await self._http_client.aclose()


mcp_client = GrafanaMCPClient()
