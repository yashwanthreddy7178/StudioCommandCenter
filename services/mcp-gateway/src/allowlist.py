"""Tool allowlist validation for Grafana MCP interactions.

Enforces server-side tool filtering to ensure strict compliance with the
hackathon constraints. Prohibits any Assistant-native operations.
"""
from __future__ import annotations

from typing import Set


# Verified against `tools/list` on mcp-grafana v1.2.0. Three names in the original
# specification did not exist on the server: `list_alert_rules` and
# `get_alert_rule_by_uid` are folded into the write-capable `alerting_manage_rules`
# and are excluded because the service account holds read-only scopes, and
# `search_tempo_traces` is really `tempo_traceql-search`.
MCP_ALLOWLIST: Set[str] = {
    "list_prometheus_metric_names",
    "list_prometheus_metric_metadata",
    "list_prometheus_label_names",
    "list_prometheus_label_values",
    "query_prometheus",
    "list_loki_label_names",
    "list_loki_label_values",
    "query_loki_logs",
    "query_loki_stats",
    "tempo_traceql-search",
    "list_incidents",
}

# Write-capable tools, reachable only through the gateway's /write endpoint and
# never through /call.
#
# The separation is the point. `validate_tool_allowed` guards the agent's path
# and rejects everything outside MCP_ALLOWLIST, which contains no write tool, so
# no prompt, jailbreak or hallucinated tool name can reach Grafana with a write.
# These are invoked only by action-executor, only after a human has approved the
# remediation, and only with arguments built in deterministic code.
MCP_WRITE_ALLOWLIST: Set[str] = {
    "create_annotation",
    "create_incident",
    "add_activity_to_incident",
}

# Tools whose `datasourceUid` argument the gateway resolves and injects, so the
# model never selects a datasource and cannot reach one belonging to another
# workspace by guessing a UID.
DATASOURCE_INJECTED_TOOLS: dict = {
    "query_prometheus": "prometheus",
    "list_prometheus_metric_names": "prometheus",
    "list_prometheus_metric_metadata": "prometheus",
    "list_prometheus_label_names": "prometheus",
    "list_prometheus_label_values": "prometheus",
    "query_loki_logs": "loki",
    "query_loki_stats": "loki",
    "list_loki_label_names": "loki",
    "list_loki_label_values": "loki",
    "tempo_traceql-search": "tempo",
}

# Explicitly banned assistant-native tools
FORBIDDEN_TOOLS: Set[str] = {
    "ask_assistant",
    "create_investigation",
    "get_investigation",
    "list_investigations",
    "assistant_chat",
}


class ToolAllowlistError(Exception):
    """Raised when a tool call violates the allowlist policy."""
    def __init__(self, tool_name: str, message: str) -> None:
        super().__init__(message)
        self.tool_name = tool_name


def validate_tool_allowed(tool_name: str) -> None:
    """Validates that the requested tool is permitted by the allowlist.
    
    Raises:
        ToolAllowlistError: If the tool is not in MCP_ALLOWLIST or is explicitly forbidden.
    """
    if tool_name in FORBIDDEN_TOOLS:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=f"Compliance violation: Assistant-native tool '{tool_name}' is strictly forbidden."
        )

    if tool_name in MCP_WRITE_ALLOWLIST:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=(
                f"Access denied: '{tool_name}' is write-capable and is not reachable "
                "on the query path. Approved writes go through /write."
            ),
        )

    if tool_name not in MCP_ALLOWLIST:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=f"Access denied: Tool '{tool_name}' is not in the Grafana MCP allowlist."
        )


def validate_write_tool_allowed(tool_name: str) -> None:
    """Validates a post-approval write against the write allowlist.

    Raises:
        ToolAllowlistError: If the tool is not a permitted write tool.
    """
    if tool_name in FORBIDDEN_TOOLS:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=f"Compliance violation: Assistant-native tool '{tool_name}' is strictly forbidden."
        )

    if tool_name not in MCP_WRITE_ALLOWLIST:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=f"Access denied: Tool '{tool_name}' is not in the Grafana MCP write allowlist."
        )
