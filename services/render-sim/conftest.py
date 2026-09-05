import sys
from pathlib import Path

# Add service directory and workspace root to sys.path
service_dir = Path(__file__).resolve().parent
workspace_root = service_dir.parent.parent

if str(service_dir) not in sys.path:
    sys.path.insert(0, str(service_dir))
if str(workspace_root) not in sys.path:
    sys.path.insert(0, str(workspace_root))

# Blank the Grafana ingest credentials before anything under `src` is imported.
#
# Importing `src.main` builds the service for real: agent-worker installs a global
# OTLP tracer provider, and render-sim constructs its exporter and starts the
# metric, log and trace pipelines. With credentials present in .env, running the
# suite published spans and samples straight into the live Grafana stack -- the
# agent traces that turned up in Tempo were produced by pytest, not by the running
# service, which is a confusing thing to debug and a worse thing to demo against.
#
# Environment variables outrank the .env file in pydantic-settings, so setting
# these to empty here disables export while leaving .env untouched.
import os

for _var in (
    "GRAFANA_OTLP_ENDPOINT_URL",
    "GRAFANA_OTLP_INSTANCE_ID",
    "GRAFANA_ACCESS_POLICY_TOKEN",
):
    os.environ[_var] = ""
