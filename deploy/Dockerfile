# Studio Production Commander - single-image deployment.
#
# All seven services run in one container, behind one nginx, as one Cloud Run
# instance. That is not a shortcut around a distributed design; it is what the
# code actually is. Run state, the evidence ledger, the audit trail and the
# tenant lease pool all live in process memory, so a second instance would not
# share them: a browser polling for run events could be routed to an instance
# that has never heard of that run. Until those move to Firestore, one instance
# is the correct deployment, and pinning it is honest rather than limiting.
#
# The Grafana MCP server ships inside the image as a sidecar process for the same
# reason it runs locally: Grafana Cloud's hosted MCP authenticates over a browser
# OAuth flow that a headless container cannot complete, while the self-hosted
# binary takes a service account token.

# ---------------------------------------------------------------- web build ---
FROM node:20-slim AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# ------------------------------------------------------- grafana mcp server ---
FROM python:3.12-slim AS mcp
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY scripts/fetch_mcp_grafana.py scripts/
# Fetches the Linux build for this architecture from Grafana's releases. The
# binary is deliberately not committed: it is a 54 MB third-party artifact with
# its own release cadence.
RUN python scripts/fetch_mcp_grafana.py

# ------------------------------------------------------------------ runtime ---
FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

WORKDIR /app

COPY services/ /app/services/

# Installed per service so each one's declared dependencies are the ones that
# get resolved, rather than a merged requirements file that hides which service
# needs what.
RUN pip install --no-cache-dir \
        /app/services/render-sim \
        /app/services/mcp-gateway \
        /app/services/impact-engine \
        /app/services/action-executor \
        /app/services/agent-worker \
        /app/services/api-gateway \
        /app/services/stream-service

COPY --from=web /web/dist /app/web/dist
COPY --from=mcp /src/.tools/mcp-grafana/mcp-grafana /usr/local/bin/mcp-grafana
COPY deploy/nginx.conf.template /app/nginx.conf.template
COPY deploy/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh /usr/local/bin/mcp-grafana \
    && mkdir -p /tmp/nginx

# Cloud Run supplies PORT; this is the local default.
ENV PORT=8080

# The MCP server is a sidecar process in this image, so its address is a property
# of the image and not something a caller should have to know. Left to the deploy
# script, any other way of starting the container -- docker run, compose, a
# colleague reproducing a bug -- came up with an unconfigured gateway and a 503
# that blamed missing configuration rather than a missing flag.
ENV GRAFANA_MCP_SERVER_URL=http://127.0.0.1:8081/mcp

CMD ["/app/entrypoint.sh"]
