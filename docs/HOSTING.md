# Hosting the Pulse API for $0 (friends & family beta)

## Decision (2026-11-24)

GCP was dropped because it asks for a deposit. The beta runs on **Oracle Cloud Always Free** plus **Supabase free**.

| Piece | Service | Notes |
|---|---|---|
| Database, auth (incl. Google login), storage | **Supabase free**, Mumbai (ap-south-1) | No Gulf region exists. |
| API, worker, Redis, HTTPS | **One OCI Always Free ARM VM** (`VM.Standard.A1.Flex`, 2 OCPU / 12 GB), Docker Compose | Always Free allows 4 OCPU / 24 GB / 200 GB total. We use half. |
| TLS | **Caddy** with Let's Encrypt on `<ip>.sslip.io` | Free hostname, no domain to buy. Swap in a real domain later by changing `API_DOMAIN`. |
| Deploy | GitHub Actions over SSH (`deploy-oci.yml`) | Rebuilds on the VM, so the image is native arm64. |
| Fallback | Render free web service (`render.yaml`) | Sleeps when idle and the worker only runs while awake. |

The stack is the same code as local dev (`QUEUE_BACKEND=arq`, Redis container, arq worker), so crons and statement parsing behave exactly as tested. The Cloud Run / Cloud Tasks path stays in the repo (`deploy-gcp.yml`, manual only) in case we want it later.

Stay inside Always Free: only the `VM.Standard.A1.Flex` shape (or `VM.Standard.E2.1.Micro`), at most 200 GB of boot plus block volume, no load balancer, no paid add-ons. Egress is 10 TB a month free.

## Things to know before you start

- **Home region is permanent.** Pick **India South (Mumbai)** or **UAE (Dubai)** at signup; Always Free resources only live in the home region. Mumbai usually has more ARM capacity; Dubai is closer to users but ARM capacity there can be scarce.
- **"Out of host capacity"** is common for A1 shapes. Retry later, try another availability domain, or start with 1 OCPU / 6 GB and resize.
- **Idle reclaim.** Oracle may reclaim Always Free instances that sit idle for 7 days (CPU, network and memory all under 20%). A quiet beta can trip it. The fix is to upgrade the account to **Pay As You Go**: nothing is charged while you stay within Always Free, and it removes reclaim.
- The $300 trial credit lasts 30 days and is not needed. Do not create anything outside the Always Free list.

## One-time setup (what only you can do)

Total: about 15 minutes. Secrets never go through chat; they go straight into GitHub.

1. **Make a deploy key** (Windows PowerShell): `ssh-keygen -t ed25519 -f $HOME\pulse_deploy -N '""'`. You get `pulse_deploy` (private) and `pulse_deploy.pub` (public).
2. **Oracle console** ([cloud.oracle.com](https://cloud.oracle.com)) > Compute > Instances > Create instance:
   - Image **Ubuntu 24.04 (aarch64)**, shape **Ampere A1.Flex, 2 OCPU / 12 GB**, boot volume 50 GB, public IPv4.
   - SSH keys: paste the contents of `pulse_deploy.pub`.
   - Show advanced options > Management > **Cloud-init script**: paste `deploy/oci/cloud-init.yaml`.
   - After it is running, VCN > subnet > Security List > add ingress TCP **80** and **443** from `0.0.0.0/0`.
3. **Supabase** (free, Mumbai). Gather: session-pooler URL (5432), transaction-pooler URL (6543), JWT secret, JWKS URL.
4. **GitHub** (repo Settings > Secrets and variables > Actions):
   - Secret `OCI_SSH_KEY`: contents of `pulse_deploy`.
   - Secret `PULSE_ENV`: your filled copy of `deploy/oci/env.example` (leave `FERNET_KEYS` empty; the VM generates and keeps its own).
   - Variables `OCI_HOST` (VM public IP) and `API_DOMAIN` (`<ip with dashes>.sslip.io`, e.g. `129-146-1-2.sslip.io`). Optional `OCI_USER` (default `ubuntu`).
5. Run the **Deploy (Oracle Cloud VM)** workflow. It waits for first boot, syncs the code, builds on the VM, migrates, and smoke-tests `https://<API_DOMAIN>/ready`. Every later push to `main` redeploys.

The VM never needs GitHub access, so the repo can stay private. To change a setting later, edit `PULSE_ENV` and re-run the workflow.

## Login with Google

Supabase Auth handles it, so the backend needs no change (it already verifies Supabase JWTs). In Supabase: Authentication > Providers > Google, using an OAuth client from the Google Cloud console (APIs & Services > Credentials). Creating an OAuth client does not need a billing account. Add the callback URL Supabase shows as an authorised redirect URI. Note for later: Apple requires "Sign in with Apple" alongside Google on iOS.

## Operating it

- Logs: `cd /opt/pulse/deploy/oci && docker compose -f docker-compose.prod.yml logs -f api worker`
- Redeploy: re-run the workflow, or `bash /opt/pulse/deploy/oci/deploy.sh` on the VM.
- Backups: statement PDFs sit in a Docker volume on the VM and the database is on Supabase free (no point-in-time recovery). Take a `pg_dump` before inviting people.

## Render fallback

`render.yaml` deploys the API with the worker in the same container and Render's free key-value store. Set `DATABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_JWKS_URL`, `FERNET_KEYS`, `CORS_ORIGINS` in the dashboard. Cold starts take 30 to 60 seconds and scheduled jobs only fire while the service is awake, so treat it as a stopgap.

## Parked: GCP Cloud Run

`deploy/bootstrap-gcp.sh`, `deploy/scheduler.sh` and `deploy-gcp.yml` with `QUEUE_BACKEND=cloudtasks` are kept but unused.
