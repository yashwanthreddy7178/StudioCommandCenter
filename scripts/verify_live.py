#!/usr/bin/env python3
"""End-to-end verification against a live Grafana Cloud stack.

Every path this checks is unit tested, and none of it had ever run against real
Grafana. Unit tests pin behaviour against doubles; they cannot tell you that a
tool name is wrong, that a service account is missing a scope, or that an
argument schema differs from what the server expects. This script answers those
questions in one run.

Checks are ordered so a failure explains the failures below it, and each one
reports PASS, FAIL or SKIP with a message naming the next action. Nothing here
mutates the render farm; the only write is one annotation, tagged for
verification and deleted again before the script exits.

Usage:
    python scripts/run_mcp_grafana.py          # in another terminal
    python scripts/verify_live.py
    python scripts/verify_live.py --keep-annotation   # leave it in Grafana
"""
from __future__ import annotations

import argparse
import json
import sys  # noqa: F401  (imported before the trust-store bootstrap below)
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent

# On a network that inspects TLS the served certificate is signed by a local root
# present in the OS store but absent from the certifi bundle httpx uses, so every
# call to Grafana fails verification. The services already delegate to the system
# trust store; this script has to do the same or it reports connection errors
# that say nothing about the system under test.
sys.path.insert(0, str(REPO_ROOT))
try:
    from services.common.tls import enable_system_trust_store

    enable_system_trust_store()
except ImportError:
    pass

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# Tools the agent path and the write path depend on. Reported individually,
# because a missing one is the difference between a working demo and a run that
# dies mid-investigation.
REQUIRED_QUERY_TOOLS = [
    "list_datasources",
    "query_prometheus",
    "query_loki_logs",
    "list_prometheus_metric_names",
]
REQUIRED_WRITE_TOOLS = [
    "create_annotation",
    "create_incident",
]
OTHER_TOOLS = ["list_incidents"]

VERIFY_TAG = "spc-verify"


def load_env(path: Path) -> Dict[str, str]:
    """Reads .env into a dict without expanding or exporting anything."""
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


class Report:
    """Collects check results and prints them as one table at the end."""

    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, name: str, status: str, message: str) -> str:
        self.rows.append((name, status, message))
        marker = {PASS: "  ok  ", FAIL: " FAIL ", SKIP: " skip "}[status]
        print(f"[{marker}] {name}: {message}", flush=True)
        return status

    def summary(self) -> int:
        print()
        print("=" * 78)
        print(f"{'Check':<34} | {'Status':<6} | Detail")
        print("-" * 78)
        for name, status, message in self.rows:
            print(f"{name:<34} | {status:<6} | {message[:severe_width()]}")
        print("-" * 78)
        failed = [r for r in self.rows if r[1] == FAIL]
        skipped = [r for r in self.rows if r[1] == SKIP]
        if failed:
            print(f"\n{len(failed)} check(s) FAILED. Fix these before the demo:")
            for name, _, message in failed:
                print(f"  - {name}: {message}")
        elif skipped:
            print(f"\nAll attempted checks passed; {len(skipped)} skipped.")
        else:
            print("\nAll checks passed.")
        print("=" * 78)
        return 1 if failed else 0


def severe_width() -> int:
    return 30


class MCPProbe:
    """Minimal MCP streamable-HTTP client, standalone by design.

    Importing the gateway's client would drag in that service's settings and
    package layout, and this script has to be runnable on its own before any
    service is up.
    """

    PROTOCOL_VERSION = "2025-06-18"

    def __init__(self, endpoint: str, token: str) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.session_id: Optional[str] = None
        self._request_id = 0
        self._client = httpx.Client(timeout=30.0, follow_redirects=True)

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        return headers

    @staticmethod
    def _parse(response: httpx.Response, request_id: Optional[int]) -> Any:
        """Decodes JSON, or the matching frame of an SSE reply."""
        if "text/event-stream" not in response.headers.get("content-type", ""):
            return response.json()
        frames = []
        for line in response.text.splitlines():
            if line.startswith("data:"):
                try:
                    frames.append(json.loads(line[5:].strip()))
                except json.JSONDecodeError:
                    continue
        for frame in frames:
            if isinstance(frame, dict) and frame.get("id") == request_id:
                return frame
        for frame in frames:
            if isinstance(frame, dict) and ("result" in frame or "error" in frame):
                return frame
        raise RuntimeError("SSE response carried no JSON-RPC reply")

    def _post(self, payload: Dict[str, Any]) -> httpx.Response:
        return self._client.post(self.endpoint, headers=self._headers(), json=payload)

    def connect(self) -> None:
        init_id = self._next_id()
        response = self._post({
            "jsonrpc": "2.0",
            "id": init_id,
            "method": "initialize",
            "params": {
                "protocolVersion": self.PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "spc-verify", "version": "0.1.0"},
            },
        })
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        body = self._parse(response, init_id)
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(str(body["error"])[:160])
        self.session_id = response.headers.get("mcp-session-id")
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> List[str]:
        list_id = self._next_id()
        body = self._parse(self._post({
            "jsonrpc": "2.0", "id": list_id, "method": "tools/list", "params": {},
        }), list_id)
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(str(body["error"])[:160])
        return [t.get("name", "") for t in body.get("result", {}).get("tools", [])]

    def tool_schema(self, tool_name: str) -> Dict[str, Any]:
        """Returns one tool's input schema, for comparing against our arguments."""
        list_id = self._next_id()
        body = self._parse(self._post({
            "jsonrpc": "2.0", "id": list_id, "method": "tools/list", "params": {},
        }), list_id)
        for tool in body.get("result", {}).get("tools", []):
            if tool.get("name") == tool_name:
                return tool.get("inputSchema", {}) or {}
        return {}

    def call(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        call_id = self._next_id()
        response = self._post({
            "jsonrpc": "2.0",
            "id": call_id,
            "method": "tools/call",
            "params": {"name": tool_name, "arguments": arguments},
        })
        if response.status_code != 200:
            raise RuntimeError(f"HTTP {response.status_code}: {response.text[:160]}")
        body = self._parse(response, call_id)
        if isinstance(body, dict) and "error" in body:
            raise RuntimeError(str(body["error"])[:200])
        result = body.get("result", {})
        if result.get("isError"):
            raise RuntimeError(f"tool returned an error result: {str(result)[:600]}")
        if "structuredContent" in result:
            return result["structuredContent"]
        texts = [b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"]
        joined = "\n".join(texts)
        try:
            return json.loads(joined)
        except (json.JSONDecodeError, TypeError):
            return joined or result

    def close(self) -> None:
        self._client.close()


class GrafanaAPI:
    """Direct Grafana HTTP API, used to confirm what actually landed.

    Reading back through the same MCP server that wrote would not prove much.
    These calls go straight to the stack.
    """

    def __init__(self, stack_url: str, token: str) -> None:
        self.base = stack_url.rstrip("/")
        self._client = httpx.Client(
            timeout=30.0,
            follow_redirects=True,
            headers={"Authorization": f"Bearer {token}"},
        )

    def get(self, path: str, **params: Any) -> httpx.Response:
        return self._client.get(f"{self.base}{path}", params=params or None)

    def delete(self, path: str) -> httpx.Response:
        return self._client.delete(f"{self.base}{path}")

    def datasource_uid(self, ds_type: str) -> Optional[str]:
        response = self.get("/api/datasources")
        if response.status_code != 200:
            return None
        preferred = {
            "prometheus": "grafanacloud-prom",
            "loki": "grafanacloud-logs",
            "tempo": "grafanacloud-traces",
        }.get(ds_type)
        entries = [d for d in response.json() if d.get("type") == ds_type]
        for entry in entries:
            if entry.get("uid") == preferred:
                return entry["uid"]
        return entries[0]["uid"] if entries else None

    def tempo_search(self, uid: str, traceql: str, lookback_sec: int = 3600) -> Any:
        """Runs a TraceQL search through the datasource proxy."""
        now = int(time.time())
        response = self._client.get(
            f"{self.base}/api/datasources/proxy/uid/{uid}/api/search",
            params={"q": traceql, "start": now - lookback_sec, "end": now, "limit": 5},
        )
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._client.close()


def check_config(report: Report, env: Dict[str, str]) -> bool:
    """Confirms the credentials every later check depends on are present."""
    required = {
        "GRAFANA_STACK_URL": "Grafana stack URL",
        "GRAFANA_MCP_SERVER_URL": "local MCP server endpoint",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "glsa_ query token",
    }
    missing = [name for name in required if not env.get(name)]
    if missing:
        report.add("config", FAIL, f"missing in .env: {', '.join(missing)}")
        return False

    ingest = ["GRAFANA_OTLP_ENDPOINT_URL", "GRAFANA_OTLP_INSTANCE_ID", "GRAFANA_ACCESS_POLICY_TOKEN"]
    missing_ingest = [name for name in ingest if not env.get(name)]
    if missing_ingest:
        report.add(
            "config", PASS,
            f"query credentials present; ingest incomplete ({', '.join(missing_ingest)})",
        )
    else:
        report.add("config", PASS, "query and ingest credentials present")
    return True


def check_mcp_tools(report: Report, probe: MCPProbe) -> Dict[str, bool]:
    """Lists the server's tools and reports which ones we actually need."""
    try:
        probe.connect()
    except Exception as exc:
        report.add(
            "mcp.connect", FAIL,
            f"{exc}. Is scripts/run_mcp_grafana.py running?",
        )
        return {}

    try:
        tools = set(probe.list_tools())
    except Exception as exc:
        report.add("mcp.connect", FAIL, f"tools/list failed: {exc}")
        return {}

    report.add("mcp.connect", PASS, f"session established, {len(tools)} tools exposed")

    available: Dict[str, bool] = {}
    for tool in REQUIRED_QUERY_TOOLS:
        available[tool] = tool in tools
    missing_query = [t for t in REQUIRED_QUERY_TOOLS if not available[t]]
    if missing_query:
        report.add("mcp.query_tools", FAIL, f"absent: {', '.join(missing_query)}")
    else:
        report.add("mcp.query_tools", PASS, "all query tools present")

    for tool in REQUIRED_WRITE_TOOLS:
        available[tool] = tool in tools
    missing_write = [t for t in REQUIRED_WRITE_TOOLS if not available[t]]
    if missing_write:
        report.add(
            "mcp.write_tools", FAIL,
            f"absent: {', '.join(missing_write)}. Add 'annotations' and 'incident' "
            "to ENABLED_TOOLS in scripts/run_mcp_grafana.py",
        )
    else:
        report.add("mcp.write_tools", PASS, "annotation and incident tools present")

    # Trace search is expected to be absent on the self-hosted build; report it
    # as information rather than a failure, since the scorecard already accounts
    # for it.
    for tool in OTHER_TOOLS:
        available[tool] = tool in tools

    available["tempo_traceql-search"] = "tempo_traceql-search" in tools
    if available["tempo_traceql-search"]:
        report.add("mcp.trace_search", PASS, "tempo_traceql-search present")
    else:
        report.add(
            "mcp.trace_search", FAIL,
            "tempo_traceql-search absent. Add 'tempo' to ENABLED_TOOLS in "
            "scripts/run_mcp_grafana.py, or set TEMPO_SEARCH_AVAILABLE=false",
        )
    return available


def resolve_uid(probe: MCPProbe, ds_type: str) -> Optional[str]:
    """Resolves a datasource UID through MCP, preferring the canonical stack one.

    A Grafana Cloud stack carries several datasources of each type, so first-of-type
    is not good enough; this mirrors what mcp-gateway does at runtime.
    """
    preferred = {
        "prometheus": "grafanacloud-prom",
        "loki": "grafanacloud-logs",
        "tempo": "grafanacloud-traces",
    }.get(ds_type)

    raw = probe.call("list_datasources", {"type": ds_type})
    if isinstance(raw, list):
        entries = raw
    elif isinstance(raw, dict):
        entries = raw.get("datasources", [])
    else:
        entries = []

    uids = [e["uid"] for e in entries if isinstance(e, dict) and e.get("uid")]
    if preferred and preferred in uids:
        return preferred
    return uids[0] if uids else None


def check_query_path(
    report: Report, probe: MCPProbe, available: Dict[str, bool]
) -> None:
    """Confirms the farm's metrics and logs are queryable through MCP."""
    if not available.get("list_datasources"):
        report.add("grafana.query", SKIP, "list_datasources unavailable")
        return

    try:
        prom_uid = resolve_uid(probe, "prometheus")
    except Exception as exc:
        report.add("grafana.query", FAIL, f"list_datasources failed: {exc}")
        return

    if not prom_uid:
        report.add("grafana.query", FAIL, "no prometheus datasource returned")
        return

    try:
        result = probe.call("query_prometheus", {
            "datasourceUid": prom_uid,
            "expr": "render_worker_frame_duration_seconds",
            "queryType": "instant",
            "endTime": "now",
        })
    except Exception as exc:
        report.add("grafana.query", FAIL, f"query_prometheus failed: {exc}")
        return

    series = result.get("data") if isinstance(result, dict) else result
    if isinstance(series, list) and series:
        report.add(
            "grafana.query", PASS,
            f"{len(series)} metric series returned from Mimir",
        )
    else:
        report.add(
            "grafana.query", FAIL,
            "query succeeded but returned no series. Is render-sim running and "
            "exporting? Allow one export interval (15s).",
        )

    if not available.get("query_loki_logs"):
        report.add("grafana.logs", SKIP, "query_loki_logs unavailable")
        return
    try:
        loki_uid = resolve_uid(probe, "loki")
        logs = probe.call("query_loki_logs", {
            "datasourceUid": loki_uid,
            "logql": '{service_name="render-sim"}',
            "limit": 5,
        })
    except Exception as exc:
        report.add("grafana.logs", FAIL, f"query_loki_logs failed: {exc}")
        return

    lines = logs.get("data") if isinstance(logs, dict) else logs
    if isinstance(lines, list) and lines:
        report.add("grafana.logs", PASS, f"{len(lines)} log line(s) returned from Loki")
    elif lines:
        report.add("grafana.logs", PASS, "log payload returned from Loki")
    else:
        report.add("grafana.logs", FAIL, "no log lines returned for render-sim")


def check_trace_search(report: Report, probe: MCPProbe, available: Dict[str, bool]) -> None:
    """Runs the agent's own trace-search tool, not just a raw Tempo query.

    The trace-attribution criterion is scored from whatever this returns, so the
    tool has to be exercised exactly as the agent calls it.
    """
    if not available.get("tempo_traceql-search"):
        report.add("mcp.trace_query", SKIP, "tempo_traceql-search unavailable")
        return
    try:
        uid = resolve_uid(probe, "tempo")
        if not uid:
            report.add("mcp.trace_query", FAIL, "no tempo datasource returned")
            return
        result = probe.call("tempo_traceql-search", {
            "datasourceUid": uid,
            "query": '{ name = "render_frame" }',
        })
    except Exception as exc:
        report.add("mcp.trace_query", FAIL, f"tempo_traceql-search failed: {exc}")
        return

    # Count the traces rather than pattern-matching the payload. An empty result
    # is `{"traces": [], "metrics": {...}}`, which contains every substring a
    # naive check would look for and reports success on no data at all.
    traces = result.get("traces") if isinstance(result, dict) else None
    if traces:
        report.add(
            "mcp.trace_query", PASS,
            f"{len(traces)} render_frame trace(s) returned through MCP",
        )
    else:
        report.add(
            "mcp.trace_query", FAIL,
            "trace search returned no render_frame spans. Is render-sim running "
            "with EMIT_RENDER_TRACES=true and ingest credentials set?",
        )


def check_traces(report: Report, api: GrafanaAPI) -> None:
    """Confirms farm traces and the agent's own spans reached Tempo."""
    try:
        uid = api.datasource_uid("tempo")
    except Exception as exc:
        report.add("tempo.render_traces", FAIL, f"datasource lookup failed: {exc}")
        report.add("tempo.agent_traces", FAIL, "skipped after datasource lookup failure")
        return
    if not uid:
        report.add("tempo.render_traces", SKIP, "no tempo datasource on this stack")
        report.add("tempo.agent_traces", SKIP, "no tempo datasource on this stack")
        return

    for label, service, hint in (
        ("tempo.render_traces", "render-sim",
         "Is render-sim running with EMIT_RENDER_TRACES=true and ingest credentials set?"),
        ("tempo.agent_traces", "agent-worker",
         "Run an investigation first; agent spans are only emitted during a run."),
    ):
        try:
            found = api.tempo_search(uid, '{ resource.service.name = "' + service + '" }')
        except Exception as exc:
            report.add(label, FAIL, f"tempo search failed: {exc}")
            continue

        traces = found.get("traces") or [] if isinstance(found, dict) else []
        if traces:
            report.add(label, PASS, f"{len(traces)} trace(s) found for {service}")
        else:
            report.add(label, FAIL, f"no traces for {service}. {hint}")


def check_write_back(
    report: Report, probe: MCPProbe, api: GrafanaAPI,
    available: Dict[str, bool], keep: bool,
) -> None:
    """Creates one tagged annotation, reads it back, and removes it again."""
    if not available.get("create_annotation"):
        report.add("grafana.writeback", SKIP, "create_annotation unavailable")
        return

    marker = uuid.uuid4().hex[:8]
    text = f"[spc-verify {marker}] write-back verification, safe to delete"

    schema = probe.tool_schema("create_annotation")
    accepted = sorted((schema.get("properties") or {}).keys())

    try:
        probe.call("create_annotation", {
            "text": text,
            "tags": [VERIFY_TAG],
            "time": int(time.time() * 1000),
        })
    except Exception as exc:
        report.add(
            "grafana.writeback", FAIL,
            f"create_annotation rejected: {exc}. Server accepts: "
            f"{', '.join(accepted) or 'unknown'}",
        )
        return

    # Read back through the Grafana API rather than through MCP: the point is to
    # prove the annotation exists in the stack, not that the server echoed us.
    found = None
    for _ in range(5):
        response = api.get("/api/annotations", tags=VERIFY_TAG, limit=20)
        if response.status_code == 200:
            for item in response.json():
                if marker in (item.get("text") or ""):
                    found = item
                    break
        if found:
            break
        time.sleep(1.0)

    if not found:
        report.add(
            "grafana.writeback", FAIL,
            "annotation accepted but not readable back. Check the service "
            "account has annotations:write.",
        )
        return

    report.add("grafana.writeback", PASS, f"annotation {found.get('id')} created and read back")

    if keep:
        report.add("grafana.cleanup", SKIP, "--keep-annotation set, leaving it in place")
        return
    deleted = api.delete(f"/api/annotations/{found.get('id')}")
    if deleted.status_code in (200, 202, 204):
        report.add("grafana.cleanup", PASS, "verification annotation removed")
    else:
        report.add(
            "grafana.cleanup", FAIL,
            f"could not delete annotation {found.get('id')}: HTTP {deleted.status_code}",
        )


def check_incidents(
    report: Report, probe: MCPProbe, api: GrafanaAPI, available: Dict[str, bool]
) -> None:
    """Reports whether opening an incident would actually work.

    Deliberately does not create one. An incident is not cleanly deletable, so a
    check that fired on every run would litter the stack. The read path and the
    presence of the Incident app together predict the write, and the failure mode
    when the app is missing is confusing enough to be worth naming: list_incidents
    answers normally while creation fails deep inside the incident service's own
    database with a foreign-key error on Counters.orgID, because the org was never
    onboarded into Grafana Incident.
    """
    if not available.get("list_incidents"):
        report.add("grafana.incidents", SKIP, "list_incidents unavailable")
        return

    try:
        probe.call("list_incidents", {"limit": 1})
    except Exception as exc:
        report.add("grafana.incidents", FAIL, f"list_incidents failed: {exc}")
        return

    # Two app ids in circulation: the unified `grafana-irm-app`, and the legacy
    # `grafana-incident-app` it replaced. Probing only the legacy id reports a
    # stack that has the current app as having none at all.
    installed = None
    for plugin_id in ("grafana-irm-app", "grafana-incident-app"):
        response = api.get(f"/api/plugins/{plugin_id}/settings")
        if response.status_code == 200:
            body = response.json()
            if body.get("enabled"):
                installed = plugin_id
                break

    if not installed:
        report.add(
            "grafana.incidents", SKIP,
            "no IRM or Incident app enabled on this stack, so creation would "
            "fail. Enable it, or set GRAFANA_INCIDENT_ENABLED=false. Annotations "
            "are unaffected.",
        )
        return

    # The app being enabled is necessary but not sufficient. The incident backend
    # keeps its own org record, created when the app is first opened, and until
    # it exists creation fails with a foreign key error on Counters.orgID while
    # list_incidents keeps answering normally. Only a real write distinguishes
    # the two, and this check does not perform one.
    report.add(
        "grafana.incidents", PASS,
        f"{installed} enabled; if creation still fails on Counters.orgID, open "
        "the IRM app once in the browser to provision the org",
    )


def check_model(report: Report, env: Dict[str, str]) -> None:
    """Confirms the configured planning model actually resolves.

    A model id that 404s is invisible until the first investigation, which is the
    worst possible moment to discover it.
    """
    model = env.get("PLANNING_MODEL") or "gemini-3.7-flash"
    project = env.get("GOOGLE_CLOUD_PROJECT")
    api_key = env.get("GEMINI_API_KEY")
    if not project and not api_key:
        report.add("gemini.model", SKIP, "no GOOGLE_CLOUD_PROJECT or GEMINI_API_KEY")
        return

    try:
        from google import genai
    except ImportError:
        report.add("gemini.model", SKIP, "google-genai not installed")
        return

    try:
        if project:
            client = genai.Client(
                vertexai=True,
                project=project,
                location=env.get("GOOGLE_CLOUD_LOCATION") or "global",
            )
        else:
            client = genai.Client(api_key=api_key)
        client.models.generate_content(model=model, contents="ping")
    except Exception as exc:
        report.add(
            "gemini.model", FAIL,
            f"'{model}' did not resolve: {str(exc)[:150]}",
        )
        return
    report.add("gemini.model", PASS, f"'{model}' resolved and responded")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--keep-annotation",
        action="store_true",
        help="leave the verification annotation in Grafana instead of deleting it",
    )
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    print()
    print("Studio Production Commander - live verification")
    print("-" * 78)

    env = load_env(REPO_ROOT / ".env")
    report = Report()

    if not check_config(report, env):
        return report.summary()

    probe = MCPProbe(
        env["GRAFANA_MCP_SERVER_URL"], env["GRAFANA_SERVICE_ACCOUNT_TOKEN"]
    )
    api = GrafanaAPI(env["GRAFANA_STACK_URL"], env["GRAFANA_SERVICE_ACCOUNT_TOKEN"])

    try:
        available = check_mcp_tools(report, probe)
        if available:
            check_query_path(report, probe, available)
            check_trace_search(report, probe, available)
            check_write_back(report, probe, api, available, args.keep_annotation)
            check_incidents(report, probe, api, available)
        check_traces(report, api)
        check_model(report, env)
    finally:
        probe.close()
        api.close()

    return report.summary()


if __name__ == "__main__":
    sys.exit(main())
