# Hosting the Pulse API for $0 (friends & family beta)

## Decision (2026-11-24)

GCP was dropped because it asks for a deposit. The beta runs on **Oracle Cloud Always Free** plus **Supabase free**.

| Piece | Service | Notes |
|---|---|---|
| Database | **Postgres in Docker on the same VM**, nightly `pg_dump` | No secret to manage, no free-tier pause. |
| Sign-in (incl. Google) and app backup | **Supabase free**, Mumbai | Backend only verifies its JWTs. |
| API, worker, Redis, HTTPS | **One OCI Always Free ARM VM** (`VM.Standard.A1.Flex`, 2 OCPU / 12 GB), Docker Compose | Always Free allows 4 OCPU / 24 GB / 200 GB total. We use half. |
| TLS | **Caddy** with Let's Encrypt on `<ip>.sslip.io` | Free hostname, no domain to buy. Swap in a real domain later by changing `API_DOMAIN`. |
| Deploy | GitHub Actions over SSH (`deploy-oci.yml`) | Rebuilds on the VM, so the image is native arm64. |
| Fallback | Render free web service (`render.yaml`) | Sleeps when idle; needs an external Postgres (e.g. Supabase) as `DATABASE_URL`. |

The stack is the same code as local dev (`QUEUE_BACKEND=arq`, Redis container, arq worker), so crons and statement parsing behave exactly as tested. The Cloud Run / Cloud Tasks path stays in the repo (`deploy-gcp.yml`, manual only) in case we want it later.

Stay inside Always Free: only the `VM.Standard.A1.Flex` shape (or `VM.Standard.E2.1.Micro`), at most 200 GB of boot plus block volume, no load balancer, no paid add-ons. Egress is 10 TB a month free.

## Things to know before you start

- **Home region is permanent.** Pick **India South (Mumbai)** or **UAE (Dubai)** at signup; Always Free resources only live in the home region. Mumbai usually has more ARM capacity; Dubai is closer to users but ARM capacity there can be scarce.
- **"Out of host capacity"** is common for A1 shapes. Retry later, try another availability domain, or start with 1 OCPU / 6 GB and resize.
- **Idle reclaim.** Oracle may reclaim Always Free instances that sit idle for 7 days (CPU, network and memory all under 20%). A quiet beta can trip it. The fix is to upgrade the account to **Pay As You Go**: nothing is charged while you stay within Always Free, and it removes reclaim.
- The $300 trial credit lasts 30 days and is not needed. Do not create anything outside the Always Free list.

## One-time setup (what only you can do)

Everything else is done by the `Deploy (Oracle Cloud)` workflow: it creates the network, security rules and VM through the OCI API, then builds and starts the stack. Secrets go straight into GitHub, never chat.

1. **SSH deploy key** (Windows PowerShell): `ssh-keygen -t ed25519 -f $HOME\pulse_deploy -N '""'`. Keep the file `pulse_deploy` (private) for step 3. The VM gets its public half automatically.
2. **OCI API key**: Oracle console > profile icon (top right) > your username > **API keys** > **Add API key** > *Generate API key pair* > download the private key, then **Add**. The "Configuration file preview" shows `user`, `tenancy`, `fingerprint` and `region`.
3. **GitHub** (repo > Settings > Secrets and variables > Actions > New repository secret). Add six secrets:

   | Secret | Value |
   |---|---|
   | `OCI_USER` | `user=` OCID from the preview |
   | `OCI_TENANCY` | `tenancy=` OCID |
   | `OCI_FINGERPRINT` | `fingerprint=` |
   | `OCI_REGION` | `region=`, e.g. `ap-mumbai-1` |
   | `OCI_KEY` | full contents of the downloaded private key `.pem` |
   | `OCI_SSH_KEY` | full contents of `pulse_deploy` |

   Optional `PULSE_ENV` for extra settings (see `deploy/oci/env.example`).
4. Run **Actions > Deploy (Oracle Cloud) > Run workflow**. The first run creates the VM (ARM capacity can take a few retries; it keeps trying for about 25 minutes) and ends with the live URL in the job summary. Every push to `main` redeploys.

The database is **Postgres on the VM**, so no database secret is needed. Every night at 07:10 Dubai time a dump is encrypted (AES-256, passphrase = SHA-256 of your `pulse_deploy` key) and uploaded to a private Always Free Object Storage bucket `pulse-backups` (newest 14 kept, plus the Fernet key). Losing the VM does not lose data, as long as you keep the `pulse_deploy` file. Supabase is only used for sign-in (Google login) and the app's encrypted backup; put its JWKS URL in `deploy/oci/public.env`.

The workflow only ever creates `VM.Standard.A1.Flex` (2 OCPU, 12 GB, 50 GB boot) plus a VCN with a free internet gateway. Nothing else is created.

## Login with Google

Supabase Auth handles it, so the backend needs no change (it already verifies Supabase JWTs). In Supabase: Authentication > Providers > Google, using an OAuth client from the Google Cloud console (APIs & Services > Credentials). Creating an OAuth client does not need a billing account. Add the callback URL Supabase shows as an authorised redirect URI. Note for later: Apple requires "Sign in with Apple" alongside Google on iOS.

## Operating it

- Logs: `cd /opt/pulse/deploy/oci && docker compose -f docker-compose.prod.yml logs -f api worker`
- Redeploy: re-run the workflow, or `bash /opt/pulse/deploy/oci/deploy.sh` on the VM.
- Backups: database dumps and the Fernet key go off-VM nightly (see above). Statement PDFs still live only on the VM disk; move them to object storage before real users upload many.
- Restore: `oci os object get -bn pulse-backups --name db/pulse-<ts>.dump.enc --file x.enc`, then `openssl enc -d -aes-256-cbc -pbkdf2 -iter 200000 -pass pass:$(sha256sum pulse_deploy | cut -d' ' -f1) -in x.enc | pg_restore -U pulse -d pulse --clean`.
- Manual backup now: `bash /opt/pulse/deploy/oci/backup.sh`.

## Render fallback

`render.yaml` deploys the API with the worker in the same container and Render's free key-value store. Set `DATABASE_URL`, `SUPABASE_JWT_SECRET`, `SUPABASE_JWKS_URL`, `FERNET_KEYS`, `CORS_ORIGINS` in the dashboard. Cold starts take 30 to 60 seconds and scheduled jobs only fire while the service is awake, so treat it as a stopgap.

## Parked: GCP Cloud Run

`deploy/bootstrap-gcp.sh`, `deploy/scheduler.sh` and `deploy-gcp.yml` with `QUEUE_BACKEND=cloudtasks` are kept but unused.
