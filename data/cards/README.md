# Card catalog

- `SCHEMA.md` data format. `banks/*.json` hand-verified sources. `catalog.json` generated (`python scripts/build_card_catalog.py`).
- `offers_feed.json` + `snapshots/` written daily by `.github/workflows/offer-watch.yml` (`scripts/offer_watch.py`).
- `community.md` unverified forum/blog findings, kept out of the data.
- Re-verify a card: open its official `sources[]` URL, update the bank file, bump `verified_on`, rebuild.
