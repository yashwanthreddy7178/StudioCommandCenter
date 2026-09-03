"""Grafana MCP upstream client.

Speaks MCP over streamable HTTP: a JSON-RPC `initialize` handshake establishes a
session, `notifications/initialized` completes it, and subsequent `tools/call`
requests carry the returned session id. Responses arrive either as JSON or as a
single server-sent event, so both shapes are parsed.

There is deliberately no local fallback. If Grafana is unreachable the call
raises and the run is reported as degraded, because a synthesised response that
the agent cannot distinguish from real telemetry would make every downstream
finding untrustworthy.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Dict, Optional

import httpx

from src.config import settings
from services.common.telemetry import setup_logging

logger = setup_logging("mcp-gateway-client")

PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "studio-production-commander", "version": "0.1.0"}

# A Grafana Cloud stack exposes several datasources of the same type: three Loki
# instances (logs, alert-state-history, usage-insights) and two Prometheus
# instances (stack metrics and billing usage). Picking the first match returns the
# wrong store, so the canonical telemetry datasource is named explicitly and the
# first-of-type search is only a fallback.
PREFERRED_DATASOURCE_UIDS: Dict[str, str] = {
    "prometheus": "grafanacloud-prom",
    "loki": "grafanacloud-logs",
    "tempo": "grafanacloud-traces",
}


class MCPUnavailableError(RuntimeError):
    """Raised when the Grafana MCP server cannot be reached or is not configured."""


class MCPToolError(RuntimeError):
    """Raised when the upstream server rejects or fails a tool call."""


def _enable_system_trust_store() -> None:
    """Verify TLS against the OS certificate store when truststore is installed.

    Networks with a TLS-inspecting proxy present a certificate signed by a local
    root that is absent from the certifi bundle. No-op inside a container.
    """
    try:
        import truststore
    except ImportError:
        return
    truststore.inject_into_ssl()


class GrafanaMCPClient:
    """Executes allowlisted Grafana MCP tool calls against Grafana Cloud."""

    def __init__(self) -> None:
        _enable_system_trust_store()
        self._http_client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._session_id: Optional[str] = None
        self._session_lock = asyncio.Lock()
        self._request_id = 0
        self._datasource_uids: Dict[str, str] = {}
        self._datasource_lock = asyncio.Lock()

    @property
    def configured(self) -> bool:
        return bool(settings.grafana_mcp_server_url and settings.grafana_service_account_token)

    @property
    def _endpoint(self) -> str:
        return settings.grafana_mcp_server_url.rstrip("/")

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Authorization": f"Bearer {settings.grafana_service_account_token}",
            "Content-Type": "application/json",
            # Streamable HTTP servers may answer with either representation.
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    @staticmethod
    def _parse_body(response: httpx.Response, request_id: Optional[int] = None) -> Any:
        """Decodes a JSON body, or the relevant frame of an SSE response.

        A streamable-HTTP server interleaves notifications with the reply, so the
        first `data:` frame is frequently something like tools/list_changed rather
        than the answer. Frames are matched on the JSON-RPC id where possible.
        """
        content_type = response.headers.get("content-type", "")
        if "text/event-stream" not in content_type:
            return response.json()

        frames = []
        for line in response.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                frames.append(json.loads(line[5:].strip()))
            except json.JSONDecodeError:
                continue

        if request_id is not None:
            for frame in frames:
                if isinstance(frame, dict) and frame.get("id") == request_id:
                    return frame
        for frame in frames:
            if isinstance(frame, dict) and ("result" in frame or "error" in frame):
                return frame
        raise MCPToolError("SSE response contained no JSON-RPC reply")

    async def _post(self, payload: Dict[str, Any]) -> httpx.Response:
        try:
            return await self._http_client.post(
                self._endpoint, headers=self._headers(), json=payload
            )
        except httpx.HTTPError as exc:
            raise MCPUnavailableError(
                f"Grafana MCP transport error: {type(exc).__name__}: {exc}"
            ) from exc

    async def _establish_session(self) -> None:
        """Performs the initialize handshake and records the session id."""
        self._session_id = None
        init_id = self._next_id()
        response = await self._post({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
        })
        if response.status_code != 200:
            raise MCPUnavailableError(
                f"Grafana MCP initialize failed: HTTP {response.status_code} "
                f"{response.text[:200]}"
            )

        body = self._parse_body(response, init_id)
        if isinstance(body, dict) and "error" in body:
            raise MCPUnavailableError(f"Grafana MCP initialize error: {body['error']}")

        self._session_id = response.headers.get("mcp-session-id")

        # Completes the handshake; servers may reject tool calls before this.
        await self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

        logger.info(
            "Grafana MCP session established",
            extra={"endpoint": self._endpoint, "session": bool(self._session_id)},
        )

    async def _ensure_session(self) -> None:
        if self._session_id is not None:
            return
        async with self._session_lock:
            if self._session_id is None:
                await self._establish_session()

    async def list_tools(self) -> list:
        """Returns the tool descriptors the upstream server exposes."""
        if not self.configured:
            raise MCPUnavailableError("Grafana MCP is not configured")
        await self._ensure_session()
        list_id = self._next_id()
        response = await self._post({
            "jsonrpc": "2.0",
            "id": list_id,
            "method": "tools/list",
            "params": {},
        })
        body = self._parse_body(response, list_id)
        if isinstance(body, dict) and "error" in body:
            raise MCPToolError(f"tools/list failed: {body['error']}")
        return body.get("result", {}).get("tools", [])

    async def resolve_datasource_uid(self, ds_type: str) -> Optional[str]:
        """Returns the UID of the first datasource of the given type, cached.

        Every Grafana MCP query tool requires a datasourceUid. Resolving it here
        rather than letting the model supply one keeps datasource selection off
        the model's surface entirely, the same reasoning as tenant injection.
        """
        if ds_type in self._datasource_uids:
            return self._datasource_uids[ds_type]

        # Resolution sits outside the singleflight pipeline, so without this lock
        # a cold start with many concurrent runs would stampede list_datasources.
        async with self._datasource_lock:
            if ds_type in self._datasource_uids:
                return self._datasource_uids[ds_type]
            return await self._resolve_datasource_uncached(ds_type)

    async def _resolve_datasource_uncached(self, ds_type: str) -> Optional[str]:
        configured = getattr(settings, f"grafana_{ds_type}_datasource_uid", "")
        if configured:
            self._datasource_uids[ds_type] = configured
            logger.info(
                "Using configured datasource",
                extra={"type": ds_type, "uid": configured},
            )
            return configured

        result = await self.call_upstream_mcp(
            "list_datasources", {"type": ds_type}, tenant_id=""
        )
        entries = [e for e in (result if isinstance(result, list) else result.get("datasources", []))
                   if isinstance(e, dict) and e.get("uid")]

        preferred = PREFERRED_DATASOURCE_UIDS.get(ds_type)
        uid = None
        if preferred:
            uid = next((e["uid"] for e in entries if e["uid"] == preferred), None)
        if uid is None:
            uid = entries[0]["uid"] if entries else None
            if uid and preferred:
                logger.warning(
                    "Preferred datasource not found, falling back to first of type",
                    extra={"type": ds_type, "preferred": preferred, "using": uid},
                )

        if uid:
            self._datasource_uids[ds_type] = uid
            logger.info(
                "Resolved Grafana datasource", extra={"type": ds_type, "uid": uid}
            )
        else:
            logger.warning("No datasource found", extra={"type": ds_type})
        return uid

    async def call_upstream_mcp(
        self, tool_name: str, parameters: Dict[str, Any], tenant_id: str
    ) -> Any:
        """Invokes one tool on the upstream Grafana MCP server."""
        if not self.configured:
            raise MCPUnavailableError(
                "Grafana MCP is not configured; set GRAFANA_MCP_SERVER_URL and "
                "GRAFANA_SERVICE_ACCOUNT_TOKEN"
            )

        await self._ensure_session()
        call_id = self._next_id()
        payload = {
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": parameters},
        }
        response = await self._post(payload)

        # An expired session is reported as 404; re-handshake once before failing.
        if response.status_code == 404 and self._session_id:
            logger.info("Grafana MCP session expired, re-establishing")
            self._session_id = None
            await self._ensure_session()
            response = await self._post(payload)

        if response.status_code != 200:
            raise MCPUnavailableError(
                f"Grafana MCP HTTP {response.status_code}: {response.text[:200]}"
            )

        body = self._parse_body(response, call_id)
        if isinstance(body, dict) and "error" in body:
            raise MCPToolError(f"{tool_name} failed: {body['error']}")

        return self._extract_result(tool_name, body.get("result", {}))

    @staticmethod
    def _extract_result(tool_name: str, result: Dict[str, Any]) -> Any:
        """Unwraps an MCP tool result into plain data.

        Prefers `structuredContent`, then a text block parsed as JSON, then the
        raw text, so callers get usable data whichever shape the server returns.
        """
        if result.get("isError"):
            raise MCPToolError(f"{tool_name} returned an error result: {result}")

        if "structuredContent" in result:
            return result["structuredContent"]

        blocks = result.get("content", [])
        texts = [b.get("text", "") for b in blocks if b.get("type") == "text"]
        if not texts:
            return result

        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except json.JSONDecodeError:
            return joined

    async def close(self) -> None:
        await self._http_client.aclose()


mcp_client = GrafanaMCPClient()
