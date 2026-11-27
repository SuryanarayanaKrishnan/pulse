# Turning on in-app bug reports (one time, free)

The endpoint `POST /v1/bug-reports` is already deployed. It answers 503 (and the app falls back to the share sheet) until the server has a target repo and token.

1. Create a **private** repo, for example `pulse-reports` (private repos are free). Screenshots are stored in it, so keep it private.
2. GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token.
   Resource owner: your account. Repository access: **Only select repositories** > `pulse-reports`.
   Permissions: Issues = Read and write, Contents = Read and write. Set an expiry (90 days).
3. Add both lines to the `PULSE_ENV` secret of the `pulse` repo (Settings > Secrets and variables > Actions), keeping the existing lines:
   `BUG_REPORT_REPO=<your-user>/pulse-reports` and `BUG_REPORT_GITHUB_TOKEN=<the token>`.
4. Run the "Deploy to Oracle" workflow (Actions tab > Run workflow).

Each report becomes an issue labelled `beta-report`, `from-app`, the category and the platform.
