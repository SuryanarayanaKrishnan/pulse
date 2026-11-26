# Changing what phones show without an app release

Three things reach every phone from the API, signed or public, with no app build:

| What | Source file | Endpoint | App behaviour |
| --- | --- | --- | --- |
| Tips, walkthrough text, bank-text (SMS) rules | `data/packs/tips.json`, `walkthrough.json`, `parser_rules.json` | `GET /v1/packs`, `/v1/packs/{name}` | Signed. Downloaded when the pack `version` is newer than the phone's copy. |
| Feature flags and tunable numbers | `data/config/remote_config.json` | `GET /v1/config` | Signed. Applied when `version` is newer. |
| Card catalog (fees, rewards, perks) | `data/cards/catalog.json` (built from `data/cards/banks/*.json`) | `GET /v1/catalog/cards` | Public reference data over HTTPS. Applied when `catalog_version` (a date) is newer. |

## The workflow (no laptop needed)

1. Open the file on github.com (pencil icon works on a phone), edit it, and **raise the version**:
   packs and config: `"version"` +1 (config uses a date number such as `20261127`); catalog: `catalog_version`
   to a later date (a same-day correction uses tomorrow's date; the date is only a label).
   Or on a computer: `python scripts/content.py bump tips` / `bump config` / `bump catalog`.
2. Open a pull request. CI runs `python scripts/content.py check --base origin/main`, which fails when a file
   changed but its version did not go up (phones would ignore it) or when JSON, a flag or a rule is invalid.
3. Merge. CI passes on `main`, the **Deploy (Oracle Cloud)** workflow redeploys the API (a few minutes). Phones pick
   the change up the next time the app opens (it refreshes in the background; cached for 5 minutes server side).

Fixing a card fee: edit the card in `data/cards/banks/<bank>.json`, run `python scripts/build_card_catalog.py`
to regenerate `data/cards/catalog.json`, bump `catalog_version`, PR, merge. The app replaces its cached copy of
that card and keeps working for cards you already own.

## Signing

Packs and config are signed with Ed25519. The key is derived from the server's Fernet key (already on the VM,
stable, backed up nightly), or set `CONFIG_SIGNING_KEY` (base64 32-byte seed) in the `PULSE_ENV` secret to pin it.
Nothing needs to be generated or paid for. The envelope carries `public_key`; the app **pins it on first contact**
(trust on first use, over HTTPS) and rejects anything signed by another key afterwards. To bake the key into the
app instead, put the public key from `GET /v1/config` (`public_key`) into `deploy/app.defines.json` as
`CONFIG_PUBLIC_KEY`. Do that only once the signing key is fixed: if the Fernet key were ever replaced without
pinning `CONFIG_SIGNING_KEY`, phones would reject content until reinstalled.

## Bank-text (SMS) rule format

`data/packs/parser_rules.json`:

```json
{"version": 2, "sms": [
  {"institution": "enbd", "direction": "debit",
   "pattern": "you spent {AMT} at {M} on card {L4}"}
]}
```

`institution` is an app parser key (`enbd`, `adcb`, `fab`, `eib`, `liv`, ...). Placeholders: `{AMT}` (required;
currency plus amount), `{L4}` card last four, `{M}` merchant, `{DATE}`. `direction` is `debit` or `credit`
(omit to infer from the wording). Rules are tried before the app's built-in wording for that bank. OTP messages
are always dropped on the phone before any rule runs.

## Production image

The Docker image copies `data/` so these files exist at runtime (`tests/test_content_ships.py` fails if a
runtime data directory is not copied). Before this was fixed `/v1/config` returned 500 on the live server.
