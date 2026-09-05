#!/usr/bin/env bash
#
# One-time setup letting GitHub Actions deploy without a service account key.
#
# Workload Identity Federation trades the runner's short-lived OIDC token for
# Google credentials. The alternative is pasting a JSON key into a repository
# secret, where it is long-lived, silently valid until someone revokes it, and
# readable by every workflow in the repo.
#
# The provider is bound to one repository by an attribute condition, so a token
# from any other repository is rejected even if it reaches this provider.
#
# Usage:
#   deploy/setup-github-oidc.sh <github-owner>/<github-repo>
set -euo pipefail

GITHUB_REPO="${1:-}"
if [[ -z "${GITHUB_REPO}" || "${GITHUB_REPO}" != */* ]]; then
    echo "Usage: deploy/setup-github-oidc.sh <owner>/<repo>"
    echo "   eg: deploy/setup-github-oidc.sh yashwanthreddy7178/StudioCommandCenter"
    exit 1
fi

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

read_env() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"'"'"'' || true; }
PROJECT="${GOOGLE_CLOUD_PROJECT:-$(read_env GOOGLE_CLOUD_PROJECT)}"
[[ -z "${PROJECT}" ]] && { echo "Set GOOGLE_CLOUD_PROJECT or put it in .env"; exit 1; }

POOL="github"
PROVIDER="github-oidc"
DEPLOYER="spc-deployer@${PROJECT}.iam.gserviceaccount.com"
RUNTIME="spc-runtime@${PROJECT}.iam.gserviceaccount.com"

gcloud config set project "${PROJECT}" >/dev/null
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"

echo "project        : ${PROJECT} (${PROJECT_NUMBER})"
echo "github repo    : ${GITHUB_REPO}"
echo

echo "==> enabling APIs"
gcloud services enable iamcredentials.googleapis.com sts.googleapis.com --quiet

echo "==> identity pool"
gcloud iam workload-identity-pools describe "${POOL}" --location=global >/dev/null 2>&1 || \
    gcloud iam workload-identity-pools create "${POOL}" \
        --location=global --display-name="GitHub Actions" --quiet

echo "==> oidc provider (restricted to ${GITHUB_REPO})"
if ! gcloud iam workload-identity-pools providers describe "${PROVIDER}" \
        --location=global --workload-identity-pool="${POOL}" >/dev/null 2>&1; then
    gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" \
        --location=global \
        --workload-identity-pool="${POOL}" \
        --display-name="GitHub OIDC" \
        --issuer-uri="https://token.actions.githubusercontent.com" \
        --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
        --attribute-condition="assertion.repository=='${GITHUB_REPO}'" \
        --quiet
fi

echo "==> deployer service account"
gcloud iam service-accounts describe "${DEPLOYER}" >/dev/null 2>&1 || \
    gcloud iam service-accounts create spc-deployer \
        --display-name="Studio Production Commander CI deployer" --quiet

# Deliberately narrow. The deployer pushes images and updates the Cloud Run
# service; it cannot read the Grafana secrets, which only the runtime account
# can, and it cannot grant itself anything further.
for role in roles/run.admin roles/artifactregistry.writer; do
    echo "    ${role}"
    gcloud projects add-iam-policy-binding "${PROJECT}" \
        --member="serviceAccount:${DEPLOYER}" \
        --role="${role}" --condition=None --quiet >/dev/null
done

# Deploying a service that runs *as* spc-runtime requires permission to act as
# it. Scoped to that one account rather than granted project-wide.
echo "    roles/iam.serviceAccountUser on ${RUNTIME}"
gcloud iam service-accounts add-iam-policy-binding "${RUNTIME}" \
    --member="serviceAccount:${DEPLOYER}" \
    --role="roles/iam.serviceAccountUser" --quiet >/dev/null

echo "==> allowing ${GITHUB_REPO} to impersonate the deployer"
gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER}" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attributes/repository/${GITHUB_REPO}" \
    --quiet >/dev/null

PROVIDER_RESOURCE="projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"

cat <<EOF

============================================================================
Done. Add these to the GitHub repository.

Settings -> Secrets and variables -> Actions

  Secrets:
    WIF_PROVIDER          ${PROVIDER_RESOURCE}
    WIF_SERVICE_ACCOUNT   ${DEPLOYER}

  Variables:
    GCP_PROJECT               ${PROJECT}
    GCP_REGION                ${REGION:-us-central1}
    GRAFANA_STACK_URL         $(read_env GRAFANA_STACK_URL)
    GRAFANA_OTLP_ENDPOINT_URL $(read_env GRAFANA_OTLP_ENDPOINT_URL)
    GRAFANA_OTLP_INSTANCE_ID  $(read_env GRAFANA_OTLP_INSTANCE_ID)

The Grafana tokens are not listed. They are already in Secret Manager and the
service reads them from there, so GitHub never holds them.

Or set them with the gh CLI:

  gh secret set WIF_PROVIDER        --body "${PROVIDER_RESOURCE}"
  gh secret set WIF_SERVICE_ACCOUNT --body "${DEPLOYER}"
  gh variable set GCP_PROJECT       --body "${PROJECT}"
  gh variable set GCP_REGION        --body "${REGION:-us-central1}"
  gh variable set GRAFANA_STACK_URL --body "$(read_env GRAFANA_STACK_URL)"
  gh variable set GRAFANA_OTLP_ENDPOINT_URL --body "$(read_env GRAFANA_OTLP_ENDPOINT_URL)"
  gh variable set GRAFANA_OTLP_INSTANCE_ID  --body "$(read_env GRAFANA_OTLP_INSTANCE_ID)"
============================================================================
EOF
