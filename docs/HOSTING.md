# Hosting the Pulse API for $0 (friends & family beta)

## Decision

| Piece | Service | Why |
|---|---|---|
| Database + auth + statement PDFs | **Supabase free** (Postgres, Auth, Storage), region **ap-south-1 (Mumbai)** | Already the plan; no Gulf region exists on Supabase. |
| API | **Google Cloud Run**, region **asia-south1 (Mumbai)** | Scales to zero, Tier 1 pricing, ~30 ms to UAE, same region as the DB. |
| Background jobs | **Cloud Tasks** (queue) + **Cloud Scheduler** (3 crons) | Replaces the always-on `arq` worker + Redis. Jobs run as HTTP calls to `/internal/*`, so the service stays scale-to-zero. |
| Secrets | **Secret Manager** | Free tier covers ~6 secrets. |
| Build / deploy | **Cloud Build + GitHub Actions** (keyless Workload Identity) | No long-lived keys in GitHub. |

Free allowances (checked 2026-11-23): Cloud Run 2M requests, 180k vCPU-s and 360k GiB-s per month; Cloud Tasks 1M operations per month; Cloud Scheduler 3 jobs per billing account (we use exactly 3). Doha (`me-central1`) is Tier 2 and the free quota is spent faster there, and Supabase has no Gulf region anyway, so Mumbai is the better pick.

Rough fit: with compute on-device, the API serves auth, sync, splits, offers and backups. 20 active users at ~500 requests/day each is ~300k requests/month, well inside the free tier. `--max-instances 2` caps any runaway.

Not used: Oracle (free VM is fine but has idle-reclaim risk and is manual to manage), Azure (free App Service/Container Apps is tighter). Cloud Run is the one Claude can drive entirely with `gcloud`.

## Queue backend

`QUEUE_BACKEND=arq` (default) keeps Redis + the arq worker for local dev. `QUEUE_BACKEND=cloudtasks` makes `enqueue_job` create a Cloud Tasks HTTP task, and Cloud Scheduler calls `/internal/cron/<name>`. Both endpoints require a Google-signed OIDC token from the `pulse-invoker` service account and return 404 when the backend is `arq`. No new Python dependencies.

## One-time setup (what only you can do)

1. **GCP project.** In the [console](https://console.cloud.google.com/projectcreate) create a project (e.g. `pulse-beta`) and attach your billing account (free trial or card; the free tier still applies). Then at Billing > Budgets & alerts create a budget of **$1** with alerts at 50% and 100%.
2. **Supabase project.** At [supabase.com](https://supabase.com/dashboard/new) create a free project in **South Asia (Mumbai)**. Collect:
   - Session pooler connection string (port 5432), used for migrations. Direct connections are IPv6-only and Cloud Run cannot reach them.
   - Transaction pooler connection string (port 6543), used by the API.
   - JWT secret (Settings > API) and JWKS URL if the project uses asymmetric keys.
3. **Bootstrap GCP** from Cloud Shell (it is already signed in) with this repo cloned:
   ```
   PROJECT_ID=pulse-beta REGION=asia-south1 deploy/bootstrap-gcp.sh
   PROJECT_ID=pulse-beta deploy/set-secrets.sh      # hidden prompts; paste values there, never in chat
   ```
   Use `postgresql+asyncpg://...` URLs (a plain `postgresql://` is also accepted). `DATABASE_URL` = transaction pooler, `MIGRATIONS_DATABASE_URL` = session pooler.
4. **GitHub variables** (repo Settings > Secrets and variables > Actions > *Variables*): the script prints `GCP_PROJECT_ID`, `GCP_REGION`, `GCP_WIF_PROVIDER`, `GCP_DEPLOYER_SA`. Also add `SUPABASE_JWKS_URL` if you use asymmetric keys and `CORS_ORIGINS` if a web client exists. No GitHub *secrets* are needed.
5. Merge to `main` (or run the Deploy workflow). It builds, runs migrations as a Cloud Run job, deploys, creates the 3 schedules and smoke-tests `/ready`.

Gmail ingestion later needs `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` and the redirect URI `https://<service-url>/v1/email-connections/gmail/callback`; add them to Secret Manager and the deploy command when that is switched on. Statement PDFs: set the `STORAGE_BACKEND=s3` variable plus the Supabase S3 keys; until then files go to ephemeral local disk and are lost on scale-down.

## Operating it

- Logs: `gcloud run services logs read pulse-api --region asia-south1`
- Roll back: `gcloud run services update-traffic pulse-api --region asia-south1 --to-revisions <rev>=100`
- Cold starts add about 2 to 4 seconds after idle; acceptable for beta. If it bothers testers, `--min-instances 1` costs roughly $5 to $8 a month.
- Supabase free projects pause after a week of inactivity; the daily Scheduler jobs keep it awake.
