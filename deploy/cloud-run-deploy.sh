#!/usr/bin/env bash
#
# The `gcloud run deploy` invocation, in one place.
#
# Both the local deploy script and the GitHub Actions workflow call this, because
# the flags below are not cosmetic: getting --no-cpu-throttling or the instance
# bounds wrong produces a service that starts, serves its health checks, and then
# fails in ways that look like application bugs. Two copies of this command would
# drift, and the copy that drifts is the one nobody runs by hand.
#
# Inputs come from the environment:
#   PROJECT, REGION, SERVICE, IMAGE, RUNTIME_SA   (required)
#   GRAFANA_STACK_URL                             (required)
#   GRAFANA_OTLP_ENDPOINT_URL, GRAFANA_OTLP_INSTANCE_ID   (optional)
set -euo pipefail

: "${PROJECT:?PROJECT is required}"
: "${REGION:?REGION is required}"
: "${SERVICE:?SERVICE is required}"
: "${IMAGE:?IMAGE is required}"
: "${RUNTIME_SA:?RUNTIME_SA is required}"
: "${GRAFANA_STACK_URL:?GRAFANA_STACK_URL is required}"

# The tokens live in Secret Manager and are referenced, never passed. Neither CI
# nor the service description ever holds their value.
SECRET_FLAGS="GRAFANA_SERVICE_ACCOUNT_TOKEN=spc-grafana-sa-token:latest"
if gcloud secrets describe spc-grafana-otlp-token --project "${PROJECT}" >/dev/null 2>&1; then
    SECRET_FLAGS="${SECRET_FLAGS},GRAFANA_ACCESS_POLICY_TOKEN=spc-grafana-otlp-token:latest"
fi

# The operator password gates the whole app, so it is referenced from Secret
# Manager like the Grafana tokens rather than passed on the command line. Without
# the secret the service falls back to the documented default password, which is
# public in .env.example -- so refuse to deploy a reachable service with it.
if gcloud secrets describe spc-app-password --project "${PROJECT}" >/dev/null 2>&1; then
    SECRET_FLAGS="${SECRET_FLAGS},APP_PASSWORD=spc-app-password:latest"
    if gcloud secrets describe spc-app-auth-secret --project "${PROJECT}" >/dev/null 2>&1; then
        SECRET_FLAGS="${SECRET_FLAGS},APP_AUTH_SECRET=spc-app-auth-secret:latest"
    fi
else
    echo "ERROR: secret 'spc-app-password' not found in project ${PROJECT}." >&2
    echo "       The service is deployed with --allow-unauthenticated, so the" >&2
    echo "       operator login is the only thing in front of it. Create it with:" >&2
    echo "" >&2
    echo "         printf '%s' 'a-long-random-password' \\" >&2
    echo "           | gcloud secrets create spc-app-password --data-file=- \\" >&2
    echo "               --project ${PROJECT}" >&2
    exit 1
fi

# --min-instances 1 --max-instances 1
#     Run state, the evidence ledger, the audit trail and the tenant leases are
#     in process memory. A second instance would not share them, and a browser
#     polling for run events could reach one that has never heard of the run.
# --no-cpu-throttling
#     render-sim ticks its worlds on a background task and an investigation
#     continues after the HTTP response returns. Cloud Run's default throttles
#     CPU to near zero between requests, which would freeze the simulator and
#     stall every run partway through.
# --timeout 3600
#     SSE connections are long-lived. The stream's own ceiling is 30 minutes.
exec gcloud run deploy "${SERVICE}" \
    --project "${PROJECT}" \
    --image "${IMAGE}" \
    --region "${REGION}" \
    --platform managed \
    --allow-unauthenticated \
    --service-account "${RUNTIME_SA}" \
    --min-instances 1 \
    --max-instances 1 \
    --no-cpu-throttling \
    --cpu 2 \
    --memory 2Gi \
    --timeout 3600 \
    --concurrency 80 \
    --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT}" \
    --set-env-vars "GOOGLE_CLOUD_LOCATION=global" \
    --set-env-vars "GOOGLE_GENAI_USE_VERTEXAI=TRUE" \
    --set-env-vars "GRAFANA_STACK_URL=${GRAFANA_STACK_URL}" \
    --set-env-vars "GRAFANA_MCP_SERVER_URL=http://127.0.0.1:8081/mcp" \
    --set-env-vars "GRAFANA_OTLP_ENDPOINT_URL=${GRAFANA_OTLP_ENDPOINT_URL:-}" \
    --set-env-vars "GRAFANA_OTLP_INSTANCE_ID=${GRAFANA_OTLP_INSTANCE_ID:-}" \
    --set-env-vars "NUM_TENANT_WORLDS=24" \
    --set-env-vars "TEMPO_SEARCH_AVAILABLE=true" \
    --set-env-vars "ENABLE_METRIC_DISCOVERY=false"     --set-env-vars "DEPLOYMENT_ORIGIN=cloud"     --set-env-vars "APP_USERNAME=${APP_USERNAME:-supervisor}" \
    --set-secrets "${SECRET_FLAGS}" \
    --quiet
