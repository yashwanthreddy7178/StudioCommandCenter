#!/usr/bin/env python3
"""Builds and uploads the project's Grafana dashboards.

The dashboards are constructed here rather than hand-maintained as JSON blobs so
that datasource UIDs are resolved against whichever stack is configured, instead
of being baked in. The generated JSON is also written to
`infra/grafana/dashboards/` so the definitions live in the repository and can be
reviewed, diffed, and imported by hand.

Two dashboards:

  Render Farm  - the telemetry the agent investigates, plus a tag-based
                 annotation query so approved remediations appear as markers on
                 the same timeline as the metrics they changed.
  Agent        - the agent's own traces, in the same stack it queries.

Series colours are a validated pair: blue for the baseline renderer, orange for
the regressed one. Colour is bound to the renderer version, not to query order,
so a filter that changes the series count cannot repaint the survivors.

Usage:
    python scripts/provision_dashboards.py            # write JSON and upload
    python scripts/provision_dashboards.py --dry-run  # write JSON only
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
try:
    from services.common.tls import enable_system_trust_store

    enable_system_trust_store()
except ImportError:
    pass

OUT_DIR = REPO_ROOT / "infra" / "grafana" / "dashboards"

# Validated against the CVD/contrast checks in both light and dark: the pair
# clears the lightness band, chroma floor, adjacent-pair separation under protan
# and tritan simulation, and 3:1 contrast on both surfaces.
BASELINE = "#2E6FD9"   # renderer v2.4.0 - the healthy half of the fleet
REGRESSED = "#D2691E"  # renderer v2.4.1 - the half carrying the regression
MUTED = "#8E9AA6"

TAG = "studio-production-commander"


def load_env(path: Path) -> Dict[str, str]:
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


# --------------------------------------------------------------------------
# Panel builders
# --------------------------------------------------------------------------

def _grid(x: int, y: int, w: int, h: int) -> Dict[str, int]:
    return {"x": x, "y": y, "w": w, "h": h}


def stat_panel(
    pid: int, title: str, expr: str, ds: str, grid: Dict[str, int],
    unit: str = "short", steps: Optional[List[Dict[str, Any]]] = None,
    description: str = "",
) -> Dict[str, Any]:
    """A headline number. No plot, so no legend and no tooltip to design."""
    return {
        "id": pid,
        "type": "stat",
        "title": title,
        "description": description,
        "datasource": {"type": "prometheus", "uid": ds},
        "gridPos": grid,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "decimals": 1,
                "color": {"mode": "thresholds"},
                "thresholds": {
                    "mode": "absolute",
                    "steps": steps or [{"color": "text", "value": None}],
                },
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "orientation": "auto",
            "colorMode": "value",
            "graphMode": "area",
            "textMode": "auto",
            "justifyMode": "auto",
        },
        "targets": [{"refId": "A", "expr": expr, "instant": True, "legendFormat": ""}],
    }


def version_split_panel(
    pid: int, title: str, metric: str, ds: str, grid: Dict[str, int],
    unit: str, description: str,
) -> Dict[str, Any]:
    """One metric, split into two coloured bands by renderer version.

    Two queries rather than one, because the split *is* the finding: the fleet
    separates into a healthy band and a regressed band, and the colour carries
    which version a worker is on. A single query with a generated palette would
    colour by series order and say nothing.
    """
    return {
        "id": pid,
        "type": "timeseries",
        "title": title,
        "description": description,
        "datasource": {"type": "prometheus", "uid": ds},
        "gridPos": grid,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 0,
                    "showPoints": "never",
                    "spanNulls": True,
                    "axisBorderShow": False,
                    "gradientMode": "none",
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byFrameRefID", "options": "A"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": BASELINE}}
                    ],
                },
                {
                    "matcher": {"id": "byFrameRefID", "options": "B"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": REGRESSED}}
                    ],
                },
            ],
        },
        "options": {
            "legend": {
                "displayMode": "list",
                "placement": "bottom",
                "showLegend": True,
                "calcs": [],
            },
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "refId": "A",
                "expr": f'{metric}{{tenant_id="$tenant", origin="$origin", renderer_version="v2.4.0"}}',
                "legendFormat": "v2.4.0  {{worker_id}}",
            },
            {
                "refId": "B",
                "expr": f'{metric}{{tenant_id="$tenant", origin="$origin", renderer_version="v2.4.1"}}',
                "legendFormat": "v2.4.1  {{worker_id}}",
            },
        ],
    }


def throughput_panel(pid: int, ds: str, grid: Dict[str, int]) -> Dict[str, Any]:
    """Observed against baseline. Same unit, so one axis - never two scales."""
    return {
        "id": pid,
        "type": "timeseries",
        "title": "Fleet throughput against baseline",
        "description": (
            "Both series are frames per minute, so they share one axis. The gap "
            "between them is the shortfall the delivery projection divides by."
        ),
        "datasource": {"type": "prometheus", "uid": ds},
        "gridPos": grid,
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 0,
                    "showPoints": "never",
                    "spanNulls": True,
                    "axisBorderShow": False,
                },
            },
            "overrides": [
                {
                    "matcher": {"id": "byFrameRefID", "options": "A"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": BASELINE}},
                        {"id": "displayName", "value": "observed"},
                    ],
                },
                {
                    "matcher": {"id": "byFrameRefID", "options": "B"},
                    "properties": [
                        {"id": "color", "value": {"mode": "fixed", "fixedColor": MUTED}},
                        {"id": "displayName", "value": "baseline"},
                        {
                            "id": "custom.lineStyle",
                            "value": {"fill": "dash", "dash": [8, 6]},
                        },
                        {"id": "custom.lineWidth", "value": 1},
                    ],
                },
            ],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "targets": [
            {
                "refId": "A",
                "expr": 'render_throughput_frames_per_minute{tenant_id="$tenant", origin="$origin"}',
                "legendFormat": "observed",
            },
            {
                "refId": "B",
                "expr": 'render_baseline_throughput_frames_per_minute{tenant_id="$tenant", origin="$origin"}',
                "legendFormat": "baseline",
            },
        ],
    }


def queue_panel(pid: int, ds: str, grid: Dict[str, int]) -> Dict[str, Any]:
    return {
        "id": pid,
        "type": "timeseries",
        "title": "Queue depth",
        "description": (
            "Frames outstanding. Rising while workers are degraded, draining once "
            "they recover - and the backlog accumulated in between is why healthy "
            "workers alone are not always enough to make the deadline."
        ),
        "datasource": {"type": "prometheus", "uid": ds},
        "gridPos": grid,
        "fieldConfig": {
            "defaults": {
                "unit": "short",
                "color": {"mode": "fixed", "fixedColor": BASELINE},
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 12,
                    "showPoints": "never",
                    "spanNulls": True,
                    "axisBorderShow": False,
                    "gradientMode": "opacity",
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"showLegend": False},
            "tooltip": {"mode": "single", "sort": "none"},
        },
        "targets": [
            {
                "refId": "A",
                "expr": 'render_queue_depth_frames{tenant_id="$tenant", origin="$origin"}',
                "legendFormat": "queue depth",
            }
        ],
    }


def logs_panel(pid: int, ds: str, grid: Dict[str, int]) -> Dict[str, Any]:
    """Farm events for the selected tenant.

    `tenant_id` arrives as structured metadata rather than a stream label, so it
    has to be a filter expression - a stream selector on it would match nothing.
    """
    return {
        "id": pid,
        "type": "logs",
        "title": "Render farm events",
        "description": (
            "The renderer_config_loaded line is the evidence behind temporal "
            "precedence: it is written once, when the change is applied, so its "
            "timestamp can be compared against the metric inflection."
        ),
        "datasource": {"type": "loki", "uid": ds},
        "gridPos": grid,
        "options": {
            "showTime": True,
            "showLabels": False,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "dedupStrategy": "none",
            "sortOrder": "Descending",
        },
        "targets": [
            {
                "refId": "A",
                "expr": '{service_name="render-sim"} | tenant_id=`$tenant` | origin=`$origin`',
                "queryType": "range",
            }
        ],
    }


def traces_panel(
    pid: int, ds: str, grid: Dict[str, int], title: str, query: str, description: str
) -> Dict[str, Any]:
    return {
        "id": pid,
        "type": "table",
        "title": title,
        "description": description,
        "datasource": {"type": "tempo", "uid": ds},
        "gridPos": grid,
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm"},
        "targets": [
            {"refId": "A", "queryType": "traceql", "query": query, "limit": 20}
        ],
    }


def text_panel(pid: int, grid: Dict[str, int], content: str) -> Dict[str, Any]:
    return {
        "id": pid,
        "type": "text",
        "title": "",
        "transparent": True,
        "gridPos": grid,
        "options": {"mode": "markdown", "content": content},
    }


# --------------------------------------------------------------------------
# Dashboards
# --------------------------------------------------------------------------

def annotations_block() -> Dict[str, Any]:
    """The built-in query plus the one that makes write-back visible.

    Approved remediations are organization annotations carrying a tag, not
    annotations bound to a dashboard id. Without a tag query here they exist in
    the stack and appear nowhere, which is exactly how they went unnoticed.
    """
    return {
        "list": [
            {
                "builtIn": 1,
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": True,
                "iconColor": "rgba(0, 211, 255, 1)",
                "name": "Annotations & Alerts",
                "type": "dashboard",
            },
            {
                "datasource": {"type": "grafana", "uid": "-- Grafana --"},
                "enable": True,
                "hide": False,
                "iconColor": REGRESSED,
                "name": "Approved remediations",
                "target": {
                    "type": "tags",
                    "matchAny": False,
                    "tags": [TAG],
                    "limit": 100,
                },
            },
        ]
    }


def tenant_variable(prom: str) -> Dict[str, Any]:
    """Tenant and origin pickers.

    `origin` exists because more than one simulator can write the same tenant
    into one Grafana stack -- a local stack and the deployed one both default to
    t01. Their series are distinguished only by this label, so without a picker
    every panel overlays a healthy fleet on a degraded one and neither is
    readable.
    """
    return {
        "list": [
            {
                "name": "origin",
                "label": "Deployment",
                "type": "query",
                "datasource": {"type": "prometheus", "uid": prom},
                "query": {
                    "qryType": 1,
                    "query": "label_values(render_fleet_total_workers, origin)",
                    "refId": "origin",
                },
                "definition": "label_values(render_fleet_total_workers, origin)",
                "refresh": 1,
                "sort": 1,
                "includeAll": False,
                "multi": False,
                "current": {},
                "options": [],
            },
            {
                "name": "tenant",
                "label": "Tenant world",
                "type": "query",
                "datasource": {"type": "prometheus", "uid": prom},
                "query": {
                    "qryType": 1,
                    "query": "label_values(render_fleet_total_workers, tenant_id)",
                    "refId": "tenant",
                },
                "definition": "label_values(render_fleet_total_workers, tenant_id)",
                "refresh": 1,
                "sort": 1,
                "includeAll": False,
                "multi": False,
                "current": {},
                "options": [],
            }
        ]
    }


def render_farm_dashboard(prom: str, loki: str) -> Dict[str, Any]:
    panels: List[Dict[str, Any]] = [
        stat_panel(
            1, "Degraded workers",
            'render_fleet_degraded_workers{tenant_id="$tenant", origin="$origin"}', prom,
            _grid(0, 0, 6, 4), unit="short",
            steps=[{"color": "green", "value": None}, {"color": "red", "value": 1}],
            description="Inferred from the fleet, never exported as a label.",
        ),
        stat_panel(
            2, "Throughput", 'render_throughput_frames_per_minute{tenant_id="$tenant", origin="$origin"}',
            prom, _grid(6, 0, 6, 4), unit="short",
            description="Frames per minute, smoothed.",
        ),
        stat_panel(
            3, "Baseline",
            'render_baseline_throughput_frames_per_minute{tenant_id="$tenant", origin="$origin"}',
            prom, _grid(12, 0, 6, 4), unit="short",
            description="What this fleet delivers when healthy.",
        ),
        stat_panel(
            4, "Queue depth", 'render_queue_depth_frames{tenant_id="$tenant", origin="$origin"}',
            prom, _grid(18, 0, 6, 4), unit="short",
            description="Frames outstanding against the delivery deadline.",
        ),
        version_split_panel(
            5, "Frame duration by renderer version",
            "render_worker_frame_duration_seconds", prom, _grid(0, 4, 12, 9), "s",
            "The localization signal. Workers on v2.4.1 separate into their own "
            "band while v2.4.0 workers hold their baseline - the control group "
            "that makes the regression attributable rather than merely present.",
        ),
        version_split_panel(
            6, "GPU utilisation by renderer version",
            "render_worker_gpu_utilization_ratio", prom, _grid(12, 4, 12, 9),
            "percentunit",
            "The mechanism. Utilisation falling alongside rising frame duration "
            "means threads stalling on memory, not compute saturation - the "
            "distinction that picks the remediation.",
        ),
        throughput_panel(7, prom, _grid(0, 13, 12, 8)),
        queue_panel(8, prom, _grid(12, 13, 12, 8)),
        logs_panel(9, loki, _grid(0, 21, 24, 9)),
    ]

    return {
        "uid": "spc-render-farm",
        "title": "Studio Production Commander — Render Farm",
        "description": (
            "The telemetry an investigation reads. Orange marks on the time axis "
            "are remediations a human approved."
        ),
        "tags": ["studio-production-commander"],
        "timezone": "browser",
        "editable": True,
        "schemaVersion": 39,
        "refresh": "10s",
        "time": {"from": "now-1h", "to": "now"},
        "annotations": annotations_block(),
        "templating": tenant_variable(prom),
        "panels": panels,
    }


def origin_variable() -> Dict[str, Any]:
    """A plain choice of deployment, for a dashboard with no Prometheus source.

    Custom rather than a label_values query: this dashboard is Tempo-backed, and
    Tempo has no cheap equivalent for enumerating a label's values.
    """
    return {
        "list": [
            {
                "name": "origin",
                "label": "Deployment",
                "type": "custom",
                "query": "local,cloud",
                "current": {"text": "local", "value": "local"},
                "options": [
                    {"text": "local", "value": "local", "selected": True},
                    {"text": "cloud", "value": "cloud", "selected": False},
                ],
                "includeAll": False,
                "multi": False,
            }
        ]
    }


def agent_dashboard(tempo: str) -> Dict[str, Any]:
    panels: List[Dict[str, Any]] = [
        text_panel(
            1, _grid(0, 0, 24, 3),
            "The agent's own traces, in the same stack it queries. A run is one "
            "`investigation` span: the LLM turns beneath it carry token counts, "
            "and each `mcp.*` span carries the tool called, its latency, and "
            "whether the gateway served it from cache instead of reaching "
            "Grafana at all.",
        ),
        traces_panel(
            2, tempo, _grid(0, 3, 24, 9), "Investigations",
            '{ resource.service.name = "agent-worker" && name = "investigation" }',
            "One row per run. Open a trace to see the turns, the tool calls, and "
            "where the time went.",
        ),
        traces_panel(
            3, tempo, _grid(0, 12, 12, 9), "MCP tool calls",
            '{ resource.service.name = "agent-worker" && name =~ "mcp.*" }',
            "Every query the agent issued to Grafana, with latency and cache "
            "outcome as span attributes.",
        ),
        traces_panel(
            4, tempo, _grid(12, 12, 12, 9), "Render farm frames",
            # Scoped by origin: a local simulator and the deployed one both
            # emit render_frame spans, and without this the panel interleaves
            # frames from two different farms.
            '{ resource.service.name = "render-sim" && span.origin = "$origin" '
            '&& name = "render_frame" }',
            "The spans behind the trace-attribution criterion. Compare "
            "gpu_render against fetch_assets and write_output inside a frame.",
        ),
    ]

    return {
        "uid": "spc-agent",
        "title": "Studio Production Commander — Agent",
        "description": (
            "Observability for the agent itself: the same stack, the same "
            "timeline, the same query language as the farm it investigates."
        ),
        "tags": ["studio-production-commander"],
        "timezone": "browser",
        "editable": True,
        "schemaVersion": 39,
        "refresh": "30s",
        "time": {"from": "now-6h", "to": "now"},
        "annotations": annotations_block(),
        "templating": origin_variable(),
        "panels": panels,
    }


# --------------------------------------------------------------------------

def resolve_uid(client: httpx.Client, base: str, ds_type: str) -> Optional[str]:
    """Resolves a datasource UID, preferring this stack's canonical one."""
    preferred = {
        "prometheus": "grafanacloud-prom",
        "loki": "grafanacloud-logs",
        "tempo": "grafanacloud-traces",
    }.get(ds_type)
    response = client.get(f"{base}/api/datasources")
    response.raise_for_status()
    uids = [d["uid"] for d in response.json() if d.get("type") == ds_type]
    if preferred and preferred in uids:
        return preferred
    return uids[0] if uids else None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="write the JSON files without uploading")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    env = load_env(REPO_ROOT / ".env")
    base = (env.get("GRAFANA_STACK_URL") or "").rstrip("/")
    token = env.get("GRAFANA_SERVICE_ACCOUNT_TOKEN", "")
    if not base or not token:
        print("Set GRAFANA_STACK_URL and GRAFANA_SERVICE_ACCOUNT_TOKEN in .env")
        return 1

    client = httpx.Client(
        timeout=30.0, follow_redirects=True,
        headers={"Authorization": f"Bearer {token}"},
    )

    try:
        prom = resolve_uid(client, base, "prometheus")
        loki = resolve_uid(client, base, "loki")
        tempo = resolve_uid(client, base, "tempo")
        print(f"datasources: prometheus={prom}  loki={loki}  tempo={tempo}")
        if not (prom and loki and tempo):
            print("Could not resolve every datasource; aborting.")
            return 1

        dashboards = [render_farm_dashboard(prom, loki), agent_dashboard(tempo)]

        OUT_DIR.mkdir(parents=True, exist_ok=True)
        for dash in dashboards:
            path = OUT_DIR / f"{dash['uid']}.json"
            path.write_text(json.dumps(dash, indent=2) + "\n", encoding="utf-8")
            print(f"wrote {path.relative_to(REPO_ROOT)}")

        if args.dry_run:
            print("\n--dry-run: nothing uploaded.")
            return 0

        print()
        failed = False
        for dash in dashboards:
            response = client.post(
                f"{base}/api/dashboards/db",
                json={"dashboard": dash, "overwrite": True,
                      "message": "provisioned by scripts/provision_dashboards.py"},
            )
            if response.status_code == 200:
                body = response.json()
                print(f"[  ok  ] {dash['title']}")
                print(f"         {base}{body.get('url')}")
            else:
                failed = True
                print(f"[ FAIL ] {dash['title']}: HTTP {response.status_code}")
                print(f"         {response.text[:400]}")
        return 1 if failed else 0
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
