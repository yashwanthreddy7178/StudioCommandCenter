#!/usr/bin/env bash
#
# Deploys Studio Production Commander to Cloud Run.
#
# Reads its Grafana settings from .env so there is one source of truth for them,
# and puts the two credentials into Secret Manager rather than passing them as
# plain environment variables, so they are not readable from the service
# description afterwards.
#
# Usage:
#   deploy/deploy.sh                 # build and deploy
#   SKIP_BUILD=1 deploy/deploy.sh    # redeploy the last image
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

SERVICE="${SERVICE:-studio-production-commander}"
REGION="${REGION:-us-central1}"
REPO="${REPO:-spc}"

read_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'' || true; }

PROJECT="${GOOGLE_CLOUD_PROJECT:-$(read_env GOOGLE_CLOUD_PROJECT)}"
GRAFANA_STACK_URL="$(read_env GRAFANA_STACK_URL)"
GRAFANA_SERVICE_ACCOUNT_TOKEN="$(read_env GRAFANA_SERVICE_ACCOUNT_TOKEN)"
GRAFANA_OTLP_ENDPOINT_URL="$(read_env GRAFANA_OTLP_ENDPOINT_URL)"
GRAFANA_OTLP_INSTANCE_ID="$(read_env GRAFANA_OTLP_INSTANCE_ID)"
GRAFANA_ACCESS_POLICY_TOKEN="$(read_env GRAFANA_ACCESS_POLICY_TOKEN)"

# Operator login and the demo quota. Read here so .env is the single source of
# truth it claims to be: cloud-run-deploy.sh expands these from the environment,
# so without exporting them a value set in .env was silently ignored and the
# service deployed on the built-in defaults.
APP_USERNAME="$(read_env APP_USERNAME)"
APP_PASSWORD="$(read_env APP_PASSWORD)"
APP_AUTH_SECRET="$(read_env APP_AUTH_SECRET)"
MAX_RUNS_PER_SESSION="$(read_env MAX_RUNS_PER_SESSION)"
MAX_RUNS_PER_DEPLOYMENT="$(read_env MAX_RUNS_PER_DEPLOYMENT)"
DEMO_CREDENTIALS_PUBLIC="$(read_env DEMO_CREDENTIALS_PUBLIC)"

for required in PROJECT GRAFANA_STACK_URL GRAFANA_SERVICE_ACCOUNT_TOKEN; do
    if [[ -z "${!required}" ]]; then
        echo "Missing ${required}. Fill it into .env (or export GOOGLE_CLOUD_PROJECT)."
        exit 1
    fi
done

IMAGE="${REGION}-docker.pkg.dev/${PROJECT}/${REPO}/${SERVICE}"
SA="spc-runtime@${PROJECT}.iam.gserviceaccount.com"

echo "project : ${PROJECT}"
echo "region  : ${REGION}"
echo "image   : ${IMAGE}"
echo

# --- one-time project setup (safe to re-run) ---------------------------------
gcloud config set project "${PROJECT}" >/dev/null

echo "==> enabling APIs"
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    aiplatform.googleapis.com \
    --quiet

echo "==> artifact registry"
gcloud artifacts repositories describe "${REPO}" --location "${REGION}" >/dev/null 2>&1 || \
    gcloud artifacts repositories create "${REPO}" \
        --repository-format=docker --location="${REGION}" \
        --description="Studio Production Commander" --quiet

echo "==> runtime service account"
gcloud iam service-accounts describe "${SA}" >/dev/null 2>&1 || \
    gcloud iam service-accounts create spc-runtime \
        --display-name="Studio Production Commander runtime" --quiet

# Vertex AI User is what lets the agent reach Gemini through Application Default
# Credentials, so no API key exists anywhere in the image or the config.
gcloud projects add-iam-policy-binding "${PROJECT}" \
    --member="serviceAccount:${SA}" \
    --role="roles/aiplatform.user" --condition=None --quiet >/dev/null

# Cloud Build runs as its own service account, and on a new project that account
# has no write access to Artifact Registry. The build then succeeds all the way
# through and fails on the final push with
# "artifactregistry.repositories.uploadArtifacts denied", which reads like a
# problem with the image rather than a missing role.
#
# Which account it is depends on when the project was created: newer projects run
# builds as the Compute Engine default service account, older ones as the legacy
# cloudbuild account. Granting whichever exists is simpler than detecting it.
echo "==> cloud build permissions"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"
for builder in     "${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"     "${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com"; do
    if gcloud iam service-accounts describe "${builder}" >/dev/null 2>&1; then
        echo "    granting artifactregistry.writer to ${builder}"
        gcloud projects add-iam-policy-binding "${PROJECT}"             --member="serviceAccount:${builder}"             --role="roles/artifactregistry.writer"             --condition=None --quiet >/dev/null
    fi
done

echo "==> secrets"
put_secret() {
    local name="$1" value="$2"
    [[ -z "${value}" ]] && return 0
    if gcloud secrets describe "${name}" >/dev/null 2>&1; then
        printf '%s' "${value}" | gcloud secrets versions add "${name}" --data-file=- --quiet >/dev/null
    else
        printf '%s' "${value}" | gcloud secrets create "${name}" --data-file=- --quiet >/dev/null
    fi
    gcloud secrets add-iam-policy-binding "${name}" \
        --member="serviceAccount:${SA}" \
        --role="roles/secretmanager.secretAccessor" --quiet >/dev/null
}
put_secret spc-grafana-sa-token "${GRAFANA_SERVICE_ACCOUNT_TOKEN}"
put_secret spc-grafana-otlp-token "${GRAFANA_ACCESS_POLICY_TOKEN}"

# The operator credential is a secret like the Grafana tokens: referenced by the
# service, never written into its description or the deploy command. An unset
# value is skipped rather than blanked, so a deployment keeps whatever is already
# in Secret Manager.
put_secret spc-app-password "${APP_PASSWORD}"
put_secret spc-app-auth-secret "${APP_AUTH_SECRET}"

# --- build -------------------------------------------------------------------
if [[ -z "${SKIP_BUILD:-}" ]]; then
    echo "==> building image with Cloud Build"
    # Built through a config rather than `--tag`, which has no way to point at a
    # Dockerfile outside the context root.
    gcloud builds submit \
        --config deploy/cloudbuild.yaml \
        --substitutions "_IMAGE=${IMAGE}" \
        . --quiet
fi

# --- deploy ------------------------------------------------------------------
# Delegated to a shared step rather than inlined here: the flags are
# load-bearing -- see the comments in cloud-run-deploy.sh -- and a second copy
# would drift from them.
echo "==> deploying"
export IMAGE REGION SERVICE PROJECT
export RUNTIME_SA="${SA}"
export GRAFANA_STACK_URL GRAFANA_OTLP_ENDPOINT_URL GRAFANA_OTLP_INSTANCE_ID
# Non-secret settings. The password and signing key are not here: they travel
# through Secret Manager above and are referenced, never passed.
export APP_USERNAME MAX_RUNS_PER_SESSION MAX_RUNS_PER_DEPLOYMENT DEMO_CREDENTIALS_PUBLIC
bash "${REPO_ROOT}/deploy/cloud-run-deploy.sh"

URL="$(gcloud run services describe "${SERVICE}" --region "${REGION}" --format='value(status.url)')"
echo
echo "deployed: ${URL}"
echo
echo "Check it:"
echo "  curl -s ${URL}/api/gateway/readyz"
echo "  curl -s ${URL}/api/sim/readyz"
echo "  open  ${URL}"
