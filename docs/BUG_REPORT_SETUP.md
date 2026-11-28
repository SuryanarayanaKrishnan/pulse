# Turning on in-app bug reports (one time, free)

`POST /v1/bug-reports` is deployed. It answers 503 (the app falls back to the share sheet) until the server has a repo and token.

Reports are filed as issues in `suryanarayanakrishnan/pulse-app`. While that repo is public, screenshots are **withheld** and the text is scrubbed of emails and long numbers. Set `BUG_REPORT_SCREENSHOTS=true` only once the repo is private (that also needs the token to have Contents read and write).

1. GitHub > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token.
   Repository access: Only select repositories > `pulse-app`. Permissions: Issues = Read and write (nothing else). Expiry 90 days.
2. In the `pulse` repo: Settings > Secrets and variables > Actions > edit `PULSE_ENV`. Keep existing lines, add
   `BUG_REPORT_REPO=suryanarayanakrishnan/pulse-app` and `BUG_REPORT_GITHUB_TOKEN=<token>`.
3. Actions > "Deploy (Oracle Cloud)" > Run workflow.

Issues get labels `beta-report`, `from-app`, the category and the platform.
