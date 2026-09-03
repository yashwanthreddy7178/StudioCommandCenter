#!/usr/bin/env python3
"""Runs the Grafana MCP server locally for development.

The upstream `mcp-grafana` binary is what mcp-gateway talks to. Grafana Cloud
also hosts an MCP server centrally, but that one authenticates through an OAuth
browser flow, which a headless Cloud Run worker cannot complete; running the
server ourselves lets it authenticate with a service account token instead.

Credentials are read from .env and passed straight into the child process
environment, never printed. Assistant-native tools are disabled at the server, so
the exclusion in architecture.md section 6.1 is enforced by the server rather
than by a prompt.

The binary is not committed. Fetch it with:
    scripts/fetch_mcp_grafana.py
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BINARY_DIR = REPO_ROOT / ".tools" / "mcp-grafana"
BINARY = BINARY_DIR / ("mcp-grafana.exe" if platform.system() == "Windows" else "mcp-grafana")
ADDRESS = "localhost:8081"

# Only the categories the gateway allowlists draw from.
#
# `annotations` and `incident` back the post-approval write path: an approved
# remediation is marked on the Grafana timeline and, when a delivery is at risk,
# opens an incident. They are write-capable, so they are reachable only through
# the gateway's /write endpoint, never from the agent's query path.
#
# `tempo` backs the trace-attribution criterion, via tempo_traceql-search.
ENABLED_TOOLS = "datasource,prometheus,loki,tempo,alerting,incident,annotations"


def load_env(path: Path) -> dict:
    values = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def resolve_stack_url(values: dict) -> str:
    """Finds the Grafana stack URL, tolerating either configured spelling."""
    stack = values.get("GRAFANA_STACK_URL", "")
    if stack:
        return stack.rstrip("/")
    mcp_url = values.get("GRAFANA_MCP_SERVER_URL", "")
    if ".grafana.net" in mcp_url:
        return mcp_url.split("/api/")[0].rstrip("/")
    return ""


def main() -> int:
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        print(f"No .env at {env_path}. Copy .env.example and fill it in.")
        return 1
    if not BINARY.exists():
        print(f"mcp-grafana not found at {BINARY}")
        print("Fetch it first:  python scripts/fetch_mcp_grafana.py")
        return 1

    values = load_env(env_path)
    stack_url = resolve_stack_url(values)
    token = values.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")

    if not stack_url or not token:
        print("Missing configuration in .env:")
        print(f"  stack url resolved : {stack_url or '<empty>'}")
        print(f"  service account token present : {bool(token)}")
        print()
        print("Set GRAFANA_STACK_URL (https://<your-stack>.grafana.net) and")
        print("GRAFANA_SERVICE_ACCOUNT_TOKEN (a glsa_ token).")
        print()
        print("Queries need read scopes. The post-approval write-back also needs")
        print("annotations:create and the incident write permission; without them")
        print("the investigation still runs and the Grafana writes are reported")
        print("as failed rather than silently skipped.")
        return 1

    child_env = os.environ.copy()
    child_env["GRAFANA_URL"] = stack_url
    child_env["GRAFANA_SERVICE_ACCOUNT_TOKEN"] = token

    print(f"grafana stack : {stack_url}")
    print(f"listening on  : http://{ADDRESS}/mcp")
    print(f"enabled tools : {ENABLED_TOOLS}")
    print()
    print("Point mcp-gateway at this with:")
    print(f"  GRAFANA_MCP_SERVER_URL=http://{ADDRESS}/mcp")
    print()

    return subprocess.call(
        [
            str(BINARY),
            "-t", "streamable-http",
            "-address", ADDRESS,
            "-enabled-tools", ENABLED_TOOLS,
            "-disable-assistant",
        ],
        env=child_env,
    )


if __name__ == "__main__":
    sys.exit(main())
