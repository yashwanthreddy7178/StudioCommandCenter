"""Server-side tenant matcher injection for PromQL, LogQL, and Tempo trace queries.

Guarantees tenant isolation at the gateway layer regardless of model prompt generation.

The queries arriving here are written by a model, and a model reading telemetry
is reading attacker-influencable text. So the matcher is never assumed to be
present because the query says it is: every function below masks string literals,
strips any tenant matcher it finds outside them, and injects its own. A query
that carries `tenant_id="t07"` inside a quoted string is left holding an inert
string and still gets a real matcher.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple


# Clause keywords whose parenthesised argument lists contain label names, not
# metric names. Injecting a matcher into `by (renderer_version)` produces invalid
# PromQL, so those spans are masked before any substitution happens.
_GROUPING_CLAUSE = re.compile(
    r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)",
    re.IGNORECASE,
)

# Bare words that are operators, modifiers or aggregations rather than metric
# names. Aggregations appear bare in the `sum by (x) (metric)` form, where they
# are not directly followed by a parenthesis.
_PROMQL_RESERVED = {
    "and", "or", "unless", "by", "without", "on", "ignoring",
    "group_left", "group_right", "offset", "bool", "inf", "nan",
    "sum", "min", "max", "avg", "group", "stddev", "stdvar",
    "count", "count_values", "bottomk", "topk", "quantile", "limitk",
    "limit_ratio",
}

_BRACE_SPAN = re.compile(r"\{[^{}]*\}")

# Every string form the query languages accept, so no matcher-shaped text inside
# a quoted span is ever read as a matcher. Backticks matter as much as quotes:
# PromQL and LogQL both accept them, and a backticked string needs no escaping,
# which is exactly what made `up{note=` + backtick + 'tenant_id="t07"' readable
# as an existing matcher.
_STRING_LITERAL = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
_BRACED_SELECTOR = re.compile(r"([a-zA-Z_:][a-zA-Z0-9_:]*)?\s*\{([^}]*)\}")
_IDENTIFIER = re.compile(r"\b[a-zA-Z_:][a-zA-Z0-9_:]*\b")

# A placeholder standing in for a masked span, used to recognise a tenant matcher
# whose value has already been masked away.
_MASKED = r"\x00\d+\x00"

# A tenant matcher in selector position, with the surrounding comma so removing
# it does not leave `{, foo="x"}` behind. Matches only a masked value, so the
# label name has to sit outside a string to be stripped.
_PROMQL_TENANT_MATCHER = re.compile(
    r"\s*,?\s*\btenant_id\s*(?:=~|!~|!=|=)\s*" + _MASKED + r"\s*,?"
)

# The same in a LogQL pipeline, where the matcher is a label-filter stage.
_LOGQL_TENANT_STAGE = re.compile(
    r"\|\s*tenant_id\s*(?:=~|!~|!=|=)\s*" + _MASKED
)

# The same in TraceQL, where it is a comparison joined by && into the filter.
_TRACEQL_TENANT_TERM = re.compile(
    r"(?:span\.|resource\.|\.)?\btenant_id\s*(?:=~|!~|!=|=)\s*" + _MASKED
    + r"\s*(?:&&|\|\|)?"
)


class _Masker:
    """Replaces spans with opaque keys and puts them back afterwards.

    Keys are digits between NUL bytes so they cannot themselves match an
    identifier and be rewritten as metric names.
    """

    def __init__(self) -> None:
        self._spans: List[Tuple[str, str]] = []

    def mask(self, pattern: re.Pattern, text: str) -> str:
        def store(match: re.Match[str]) -> str:
            key = f"\x00{len(self._spans)}\x00"
            self._spans.append((key, match.group(0)))
            return key
        return pattern.sub(store, text)

    def restore(self, text: str) -> str:
        # Outermost spans first: a masked brace span holds the placeholder of any
        # string nested inside it, so a single forward pass would leave the inner
        # key stranded in the output.
        for key, original in reversed(self._spans):
            text = text.replace(key, original)
        while "\x00" in text:
            before = text
            for key, original in self._spans:
                text = text.replace(key, original)
            if text == before:
                break
        return text


def _tidy_selectors(text: str) -> str:
    """Cleans up the punctuation left behind by removing a matcher."""
    text = re.sub(r"\{\s*,\s*", "{", text)
    text = re.sub(r"\s*,\s*\}", "}", text)
    text = re.sub(r",\s*,", ",", text)
    return text


def inject_tenant_promql(query: str, tenant_id: str) -> str:
    """Constrains every metric selector in a PromQL expression to one tenant.

    Handles real expressions, not just bare metric names: aggregations, grouping
    clauses, function calls and range selectors all appear once a model is
    writing the queries. Identifiers are only treated as metric names when they
    are not function calls, not operators, and not inside a grouping clause, so
    `avg(m) by (renderer_version)` constrains `m` and leaves the grouping label
    untouched.

    Any tenant matcher already in the expression is removed before ours is added,
    so the result is the same whether the model supplied one, supplied a
    different tenant's, or supplied none. Re-injecting is therefore idempotent.
    """
    if not query or not tenant_id:
        return query

    matcher = f'tenant_id="{tenant_id}"'
    masker = _Masker()

    # Mask spans that must never be rewritten, innermost concern first.
    masked = masker.mask(_STRING_LITERAL, query)

    # Drop any tenant matcher the caller supplied. Its value is masked by now, so
    # only a real matcher outside a string can match here.
    masked = _tidy_selectors(_PROMQL_TENANT_MATCHER.sub("", masked))

    masked = masker.mask(_GROUPING_CLAUSE, masked)

    # Selectors that already carry a label set: add the matcher inside the braces.
    def add_to_braces(match: re.Match[str]) -> str:
        metric = match.group(1) or ""
        inner = match.group(2).strip()
        return f"{metric}{{{matcher}, {inner}}}" if inner else f"{metric}{{{matcher}}}"

    masked = _BRACED_SELECTOR.sub(add_to_braces, masked)

    # Mask every label set, including the ones just written, so the identifier
    # pass below cannot descend into them and rewrite label names as metrics.
    masked = masker.mask(_BRACE_SPAN, masked)

    # Remaining bare identifiers denote metrics and get a fresh label set.
    def add_to_bare(match: re.Match[str]) -> str:
        name = match.group(0)
        end = match.end()
        if name.lower() in _PROMQL_RESERVED:
            return name
        # A trailing '(' makes this a function call, not a metric.
        if masked[end:end + 1] == "(":
            return name
        # A trailing placeholder means a label set was already attached in the
        # pass above and masked; injecting again would duplicate it.
        if masked[end:end + 1] == "\x00":
            return name
        return f"{name}{{{matcher}}}"

    masked = _IDENTIFIER.sub(add_to_bare, masked)
    return masker.restore(masked)


# A bare metric selector, optionally with a label set: the shape the agent's
# queries actually take. Anything more complex is left alone.
_BARE_SELECTOR = re.compile(r"^\s*[a-zA-Z_:][a-zA-Z0-9_:]*\s*(\{[^{}]*\})?\s*$")

# How recently a series must have reported to count as live.
RECENCY_WINDOW = "1m"


def enforce_recency(expr: str) -> str:
    """Restricts an instant query to series that are still being written.

    Changing a label value forks a series: a worker moving from renderer_version
    v2.4.0 to v2.4.1 leaves the old series in place, and an instant query returns
    both for the whole lookback window. Both carry the query timestamp rather than
    the sample timestamp, so the stale one cannot be told apart after the fact and
    a rolled-back worker can read as still degraded.

    `last_over_time(expr[1m])` returns only series with a sample inside the window,
    which drops the abandoned fork. Applied only to bare selectors: wrapping an
    expression that already carries a range vector, such as rate(m[5m]), would not
    be valid PromQL.
    """
    if not expr or not _BARE_SELECTOR.match(expr):
        return expr
    return f"last_over_time({expr.strip()}[{RECENCY_WINDOW}])"


def inject_tenant_logql(query: str, tenant_id: str) -> str:
    """Constrains a LogQL query to one tenant.

    OTLP log record attributes arrive in Loki as structured metadata, not as
    stream labels, so `tenant_id` has to be applied as a pipeline label filter.
    Adding it inside the stream selector instead matches no stream at all, which
    silently returns an empty result rather than an isolated one.

    As in the PromQL case, any tenant filter already present is stripped first,
    so a filter the model wrote cannot stand in for the one the gateway owes.
    """
    if not query or not tenant_id:
        return query

    masker = _Masker()
    masked = masker.mask(_STRING_LITERAL, query)
    masked = _LOGQL_TENANT_STAGE.sub("", masked)
    # Removing a stage leaves its surrounding whitespace behind, and the insert
    # below adds its own. Without collapsing, re-injecting the same query grows a
    # space each time and the round-trip is no longer stable. Safe here because
    # every string literal is masked, so no run of spaces inside one is touched.
    masked = re.sub(r"\s+", " ", masked).strip()

    tenant_filter = f'| tenant_id="{tenant_id}"'

    # The label filter belongs immediately after the stream selector, before any
    # line filters already present in the pipeline.
    closing = masked.find("}")
    if masked.lstrip().startswith("{") and closing != -1:
        head, tail = masked[: closing + 1], masked[closing + 1:]
        return masker.restore(f"{head} {tenant_filter}{tail}".rstrip())

    # A bare pipeline with no stream selector cannot be safely constrained.
    return masker.restore(f'{{tenant_id="{tenant_id}"}} {masked}'.strip())


def inject_tenant_traceql(query: str, tenant_id: str) -> str:
    """Constrains a TraceQL query to one tenant.

    render-sim writes tenant_id as a span attribute rather than a resource
    attribute, so the matcher is `span.tenant_id`; the resource-scoped spelling
    would match nothing and silently return an empty result rather than an error.

    Only the first brace group is rewritten, so a query that continues into an
    aggregate such as `{ ... } | count() > 2` keeps its pipeline intact.

    A tenant term already in the filter is removed rather than trusted. The
    previous check accepted the bare substring `tenant_id` anywhere in the query,
    so `{ name = "tenant_id" }` skipped injection entirely and read every
    tenant's spans.
    """
    if not query or not tenant_id:
        return query

    matcher = f'span.tenant_id = "{tenant_id}"'

    masker = _Masker()
    masked = masker.mask(_STRING_LITERAL, query)
    masked = _TRACEQL_TENANT_TERM.sub("", masked)
    # Remove a && or || left dangling where a term used to be.
    masked = re.sub(r"\{\s*(?:&&|\|\|)\s*", "{ ", masked)
    masked = re.sub(r"\s*(?:&&|\|\|)\s*\}", " }", masked)

    match = re.match(r"\s*\{([^}]*)\}(.*)$", masked, re.DOTALL)
    if not match:
        # No selector to extend, so the tenant matcher becomes the whole filter.
        return "{ " + matcher + " }"

    inner = match.group(1).strip()
    tail = match.group(2)
    if inner:
        return masker.restore("{ " + matcher + " && " + inner + " }" + tail)
    return masker.restore("{ " + matcher + " }" + tail)


def rewrite_tool_parameters(tool_name: str, params: Dict[str, Any], tenant_id: str) -> Dict[str, Any]:
    """Rewrites parameters for MCP tools to strictly enforce tenant isolation."""
    rewritten = params.copy()

    # PromQL query tools
    if tool_name in {"query_prometheus", "query_prometheus_histogram"}:
        for field in ("query", "expr"):
            if field in rewritten and isinstance(rewritten[field], str):
                scoped = inject_tenant_promql(rewritten[field], tenant_id)
                # Recency is applied after tenant injection so the label matcher
                # ends up inside the selector rather than outside the wrapper.
                rewritten[field] = enforce_recency(scoped)

    # LogQL query tools
    elif tool_name in {"query_loki_logs", "query_loki_stats"}:
        if "query" in rewritten and isinstance(rewritten["query"], str):
            rewritten["query"] = inject_tenant_logql(rewritten["query"], tenant_id)
        if "logql" in rewritten and isinstance(rewritten["logql"], str):
            rewritten["logql"] = inject_tenant_logql(rewritten["logql"], tenant_id)

    # Tempo trace search tools.
    #
    # This branch previously keyed on "search_tempo_traces", a name no Grafana MCP
    # server exposes. It therefore never fired, and every trace query left the
    # gateway with no tenant matcher at all, reading every tenant's spans. The
    # real tool is `tempo_traceql-search`, confirmed against a live tools/list.
    elif tool_name in {"tempo_traceql-search", "traceql_search", "search_tempo_traces"}:
        for field in ("query", "traceql", "q"):
            if field in rewritten and isinstance(rewritten[field], str):
                rewritten[field] = inject_tenant_traceql(rewritten[field], tenant_id)

    # Label values query tools (ensure tenant_id filter is passed)
    elif tool_name in {"list_prometheus_label_values", "list_loki_label_values"}:
        rewritten["match"] = f'tenant_id="{tenant_id}"'

    return rewritten
