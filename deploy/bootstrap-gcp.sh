#!/usr/bin/env bash
# One-time GCP setup for the Pulse API. Idempotent; safe to re-run.
# Usage: PROJECT_ID=pulse-beta REGION=asia-south1 deploy/bootstrap-gcp.sh
set -euo pipefail
: "${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-asia-south1}"
GH_REPO="${GH_REPO:-suryanarayanakrishnan/pulse}"
SERVICE="${SERVICE:-pulse-api}"
gcloud config set project "$PROJECT_ID" >/dev/null

gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudtasks.googleapis.com cloudscheduler.googleapis.com secretmanager.googleapis.com \
  iam.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

gcloud artifacts repositories describe pulse --location "$REGION" >/dev/null 2>&1 || \
  gcloud artifacts repositories create pulse --repository-format=docker --location "$REGION"

sa() { gcloud iam service-accounts describe "$1@$PROJECT_ID.iam.gserviceaccount.com" >/dev/null 2>&1 || \
  gcloud iam service-accounts create "$1" --display-name "$2"; }
sa pulse-runtime "Pulse API runtime"
sa pulse-invoker "Pulse Cloud Tasks/Scheduler invoker"
sa pulse-deployer "Pulse GitHub deployer"
RT="pulse-runtime@$PROJECT_ID.iam.gserviceaccount.com"
INV="pulse-invoker@$PROJECT_ID.iam.gserviceaccount.com"
DEP="pulse-deployer@$PROJECT_ID.iam.gserviceaccount.com"
bind() { gcloud projects add-iam-policy-binding "$PROJECT_ID" --member "serviceAccount:$1" --role "$2" --condition=None >/dev/null; }
bind "$RT" roles/secretmanager.secretAccessor
bind "$RT" roles/cloudtasks.enqueuer
bind "$DEP" roles/run.admin
bind "$DEP" roles/artifactregistry.writer
bind "$DEP" roles/cloudbuild.builds.editor
bind "$DEP" roles/cloudscheduler.admin
bind "$DEP" roles/cloudtasks.admin
bind "$DEP" roles/secretmanager.secretAccessor
bind "$DEP" roles/storage.admin   # Cloud Build source bucket
gcloud iam service-accounts add-iam-policy-binding "$RT" --member "serviceAccount:$DEP" --role roles/iam.serviceAccountUser >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$INV" --member "serviceAccount:$DEP" --role roles/iam.serviceAccountUser >/dev/null
gcloud iam service-accounts add-iam-policy-binding "$INV" --member "serviceAccount:$RT" --role roles/iam.serviceAccountUser >/dev/null

# Cloud Tasks queue: 3 attempts, backoff, modest rate (cost guard)
gcloud tasks queues describe pulse-jobs --location "$REGION" >/dev/null 2>&1 || \
  gcloud tasks queues create pulse-jobs --location "$REGION" --max-attempts 3 --min-backoff 10s --max-backoff 300s \
    --max-dispatches-per-second 5 --max-concurrent-dispatches 5

# Keyless GitHub -> GCP auth (Workload Identity Federation), restricted to this repo
gcloud iam workload-identity-pools describe github --location global >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools create github --location global --display-name "GitHub Actions"
gcloud iam workload-identity-pools providers describe github-oidc --location global --workload-identity-pool github >/dev/null 2>&1 || \
  gcloud iam workload-identity-pools providers create-oidc github-oidc --location global --workload-identity-pool github \
    --issuer-uri https://token.actions.githubusercontent.com \
    --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository" \
    --attribute-condition "assertion.repository=='$GH_REPO'"
PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format 'value(projectNumber)')"
gcloud iam service-accounts add-iam-policy-binding "$DEP" --role roles/iam.workloadIdentityUser \
  --member "principalSet://iam.googleapis.com/projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$GH_REPO" >/dev/null

# Secrets: created empty here; fill with deploy/set-secrets.sh (values never go through chat or git)
for s in DATABASE_URL MIGRATIONS_DATABASE_URL SUPABASE_JWT_SECRET FERNET_KEYS GOOGLE_CLIENT_SECRET S3_SECRET_ACCESS_KEY S3_ACCESS_KEY_ID; do
  gcloud secrets describe "$s" >/dev/null 2>&1 || gcloud secrets create "$s" --replication-policy automatic
done

echo
echo "Add these GitHub repo secrets/variables (Settings > Secrets and variables > Actions):"
echo "  GCP_PROJECT_ID = $PROJECT_ID"
echo "  GCP_REGION     = $REGION"
echo "  GCP_WIF_PROVIDER = projects/$PROJECT_NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc"
echo "  GCP_DEPLOYER_SA  = $DEP"
