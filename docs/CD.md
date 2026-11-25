# Continuous delivery (backend)

Merge to `main` -> `CI` (lint, types, migrations, tests) -> if green, `Deploy (Oracle Cloud)` ships to the VM -> smoke test hits `/ready`.

- Deploy is a `workflow_run` of `CI`, so a red build never ships. Pull-request CI runs never deploy.
- Docs-only pushes (`docs/**`, `*.md`) are skipped.
- Deploys are serialised (`concurrency: deploy-oci`) and can be started by hand from the Actions tab.
- Without the OCI secrets the deploy step prints a notice and exits green (see `docs/HOSTING.md`).
- Rollback: revert the commit on `main`; the loop redeploys the previous state.
- Cost: Linux minutes only (1x quota); no paid services.

The app side of the loop (APK and IPA releases for testers) lives in `pulse-app/docs/CD.md`.
