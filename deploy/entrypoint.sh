#!/usr/bin/env bash
#
# Starts the Grafana MCP server, the seven services, and nginx in one container.
#
# Every process is a child of this script, and the script exits as soon as any
# one of them does. That is deliberate: nginx would happily keep serving 502s
# for a dead agent-worker, and the instance would look healthy while doing
# nothing useful. Exiting hands the problem to Cloud Run, which replaces the
# instance.
set -euo pipefail

PORT="${PORT:-8080}"

log() { echo "[entrypoint] $*"; }

# --- Grafana MCP server ------------------------------------------------------
# Same reasoning as the local runner: the hosted Cloud MCP server authenticates
# through a browser OAuth flow that no container can complete, so the
# self-hosted binary runs here with a service account token instead.
if [[ -n "${GRAFANA_STACK_URL:-}" && -n "${GRAFANA_SERVICE_ACCOUNT_TOKEN:-}" ]]; then
    log "starting mcp-grafana against ${GRAFANA_STACK_URL}"
    GRAFANA_URL="${GRAFANA_STACK_URL}" \
    GRAFANA_SERVICE_ACCOUNT_TOKEN="${GRAFANA_SERVICE_ACCOUNT_TOKEN}" \
    mcp-grafana \
        -t streamable-http \
        -address 127.0.0.1:8081 \
        -enabled-tools "datasource,prometheus,loki,tempo,alerting,incident,annotations" \
        -disable-assistant &
else
    log "WARNING: GRAFANA_STACK_URL or GRAFANA_SERVICE_ACCOUNT_TOKEN unset;"
    log "         the MCP server will not start and investigations will fail."
fi

# --- application services ----------------------------------------------------
# `src.*` resolves from each service directory and `services.common.*` from /app,
# which is why each one is started from its own working directory rather than
# from a single shared root.
export PYTHONPATH=/app

start_service() {
    local name="$1" port="$2"
    log "starting ${name} on 127.0.0.1:${port}"
    (
        cd "/app/services/${name}"
        exec uvicorn src.main:app \
            --host 127.0.0.1 \
            --port "${port}" \
            --log-level warning
    ) &
}

# render-sim first: it owns the simulated worlds everything else reads, and it
# needs a tick or two before its telemetry is queryable.
start_service render-sim      8004
start_service mcp-gateway     8001
start_service impact-engine   8002
start_service action-executor 8003
start_service agent-worker    8010
start_service stream-service  8005
start_service api-gateway     8000

# --- nginx -------------------------------------------------------------------
# /tmp is a fresh tmpfs on a new instance, so the scratch directories the config
# points at have to be created here rather than only in the image.
mkdir -p /tmp/nginx

# nginx cannot read an environment variable, and Cloud Run only tells us the port
# through one.
sed "s/__PORT__/${PORT}/" /app/nginx.conf.template > /tmp/nginx/nginx.conf
log "starting nginx on :${PORT}"
nginx -c /tmp/nginx/nginx.conf -g 'daemon off;' &

# --- supervision -------------------------------------------------------------
trap 'log "terminating"; kill 0' TERM INT

wait -n
log "a process exited; shutting the instance down so it gets replaced"
kill 0 2>/dev/null || true
exit 1
