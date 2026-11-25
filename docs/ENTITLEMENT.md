# Free vs Plus card intel

`data/cards/intel.json` items carry a `tier`:

- `free`: shown to everyone, including users with no cards (default stacking combos).
- `plus`: niche detail (rare combos, time-bound caveats, offer changes). A missing tier means `plus`.

`GET /v1/cards/combos` returns every active item for any signed-in user with its `tier`. `GET /v1/cards/intel`
(items matched to the user's own cards) also carries `tier`.

**The server only labels tiers during the friends-and-family beta.** The app decides what to unlock using its
`EntitlementService` (a mock that flips a local flag, no real payment). When real billing arrives, swap that
service for one backed by the billing provider and make this endpoint withhold `body` and `sources` of `plus`
items unless the user is entitled. No other code needs to change.

The app bundles the starter set (`scripts/export_app_combos.py`) so it works offline.
