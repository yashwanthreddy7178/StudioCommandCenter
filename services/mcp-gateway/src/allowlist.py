"""Tool allowlist validation for Grafana MCP interactions.

Enforces server-side tool filtering to ensure strict compliance with the
hackathon constraints. Prohibits any Assistant-native operations.
"""
from __future__ import annotations

from typing import Set


MCP_ALLOWLIST: Set[str] = {
    "list_prometheus_metric_names",
    "list_prometheus_label_names",
    "list_prometheus_label_values",
    "query_prometheus",
    "query_prometheus_histogram",
    "list_loki_label_names",
    "list_loki_label_values",
    "query_loki_logs",
    "query_loki_stats",
    "search_tempo_traces",
    "list_alert_rules",
    "get_alert_rule_by_uid",
    "list_incidents",
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

    if tool_name not in MCP_ALLOWLIST:
        raise ToolAllowlistError(
            tool_name=tool_name,
            message=f"Access denied: Tool '{tool_name}' is not in the Grafana MCP allowlist."
        )
