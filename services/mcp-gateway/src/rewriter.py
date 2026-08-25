"""Server-side tenant matcher injection for PromQL, LogQL, and Tempo trace queries.

Guarantees tenant isolation at the gateway layer regardless of model prompt generation.
"""
from __future__ import annotations

import re
from typing import Any, Dict


def inject_tenant_promql(query: str, tenant_id: str) -> str:
    """Injects tenant_id matcher into PromQL metric selectors."""
    if not query or not tenant_id:
        return query

    # If tenant_id is already present with correct value, avoid duplicating
    if f'tenant_id="{tenant_id}"' in query:
        return query

    # Match metric selectors: metric_name{...} or metric_name without braces
    # 1. Metric with braces: foo{bar="baz"} -> foo{tenant_id="t01", bar="baz"}
    def replace_braced(match: re.Match[str]) -> str:
        prefix = match.group(1) # metric name or empty
        inner = match.group(2).strip()
        if inner:
            return f'{prefix}{{tenant_id="{tenant_id}", {inner}}}'
        else:
            return f'{prefix}{{tenant_id="{tenant_id}"}}'

    # Pattern for braced selectors: [a-zA-Z_:][a-zA-Z0-9_:]*\{[^\}]*\}
    if "{" in query and "}" in query:
        query = re.sub(r'([a-zA-Z_:][a-zA-Z0-9_:]*)\s*\{([^}]*)\}', replace_braced, query)
    else:
        # Bare metric names like `render_queue_depth_frames`
        def replace_bare(match: re.Match[str]) -> str:
            name = match.group(0)
            # Avoid replacing keywords like sum, rate, avg, count, by, without
            keywords = {"sum", "rate", "avg", "count", "min", "max", "by", "without", "irate", "increase"}
            if name.lower() in keywords:
                return name
            return f'{name}{{tenant_id="{tenant_id}"}}'

        query = re.sub(r'\b[a-zA-Z_][a-zA-Z0-9_]*\b', replace_bare, query)

    return query


def inject_tenant_logql(query: str, tenant_id: str) -> str:
    """Injects tenant_id matcher into LogQL stream selectors."""
    if not query or not tenant_id:
        return query

    if f'tenant_id="{tenant_id}"' in query:
        return query

    # Stream selector in LogQL is inside { ... }
    # E.g. {job="render"} |= "error" -> {job="render", tenant_id="t01"} |= "error"
    def replace_logql_stream(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        if inner:
            return f'{{tenant_id="{tenant_id}", {inner}}}'
        else:
            return f'{{tenant_id="{tenant_id}"}}'

    if "{" in query and "}" in query:
        return re.sub(r'\{([^}]*)\}', replace_logql_stream, query, count=1)
    else:
        return f'{{tenant_id="{tenant_id}"}} {query}'


def rewrite_tool_parameters(tool_name: str, params: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """Rewrites parameters for MCP tools to strictly enforce tenant isolation."""
    rewritten = params.copy()

    # PromQL query tools
    if tool_name in {"query_prometheus", "query_prometheus_histogram"}:
        if "query" in rewritten and isinstance(rewritten["query"], str):
            rewritten["query"] = inject_tenant_promql(rewritten["query"], tenant_id)
        if "expr" in rewritten and isinstance(rewritten["expr"], str):
            rewritten["expr"] = inject_tenant_promql(rewritten["expr"], tenant_id)

    # LogQL query tools
    elif tool_name in {"query_loki_logs", "query_loki_stats"}:
        if "query" in rewritten and isinstance(rewritten["query"], str):
            rewritten["query"] = inject_tenant_logql(rewritten["query"], tenant_id)
        if "logql" in rewritten and isinstance(rewritten["logql"], str):
            rewritten["logql"] = inject_tenant_logql(rewritten["logql"], tenant_id)

    # Tempo trace search tools
    elif tool_name == "search_tempo_traces":
        tags = rewritten.get("tags", {})
        if isinstance(tags, dict):
            tags["tenant_id"] = tenant_id
            rewritten["tags"] = tags
        elif isinstance(tags, str):
            if "tenant_id" not in tags:
                rewritten["tags"] = f'tenant_id="{tenant_id}" {tags}'.strip()

    # Label values query tools (ensure tenant_id filter is passed)
    elif tool_name in {"list_prometheus_label_values", "list_loki_label_values"}:
        rewritten["match"] = f'tenant_id="{tenant_id}"'

    return rewritten
