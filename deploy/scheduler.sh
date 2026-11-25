#!/usr/bin/env bash
# Creates/updates the 3 Cloud Scheduler cron jobs (exactly the free allowance per billing account).
# Called by the deploy workflow after the service URL is known.
set -euo pipefail
: "${PROJECT_ID:?}" "${REGION:?}" "${SERVICE_URL:?}"
INV="pulse-invoker@$PROJECT_ID.iam.gserviceaccount.com"
upsert() { # name schedule path
  local verb=create
  gcloud scheduler jobs describe "$1" --location "$REGION" >/dev/null 2>&1 && verb=update
  gcloud scheduler jobs $verb http "$1" --location "$REGION" --schedule "$2" --time-zone UTC \
    --uri "$SERVICE_URL/internal/cron/$3" --http-method POST \
    --oidc-service-account-email "$INV" --oidc-token-audience "$SERVICE_URL" \
    --attempt-deadline 300s --max-retry-attempts 2 >/dev/null
}
upsert pulse-gmail-sync   "5 0,6,12,18 * * *" sync_all_gmail_connections
upsert pulse-networth     "30 20 * * *"       snapshot_net_worth   # 00:30 Dubai
upsert pulse-valuations   "20 1,7,13,19 * * *" refresh_valuations
upsert pulse-vault-warn   "40 * * * *" warn_vault_owners
