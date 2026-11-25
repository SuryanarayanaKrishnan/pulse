# Card intel: community tips that feed the recommender

Core idea (surya): stacking combos and temporary caveats are discovered in places like r/UAECreditCards, so
the intel feed is an input to the whole card recommendation engine, not only a list of tips.

## Item kinds (`data/cards/intel.json`)

| kind | example | used by |
|---|---|---|
| `caveat` | SIB Cashback pays 10% on Tabby only when paid via Apple/Google Pay; may earn 0.5% otherwise | warning on the card + rate override in the ranking |
| `combo` | pair of cards that work together (`card_ids` with the roles in `effect`) | "add card Y to stack with X" suggestions |
| `bonus` | new welcome offer | offer alerts |
| `offer_change` | rate/cap/terms change from a date | offer alerts + `valid_until` expiry |

Every item has dates (`valid_from`, `valid_until`, `review_by`), a `confidence` (`reported`, `corroborated`,
`verified`), sources, and optional `effect`. Temporary caveats expire by themselves and surface a review date.

## Recommender hook

`card_intel.recommender_adjustments()` returns active `rate_override` effects with a trust weight
(reported 0.5, corroborated 0.8, verified 1.0). The engine blends `trust * override + (1 - trust) * catalog_rate`,
so one unverified report lowers a card's rank without removing it. Wiring that blend into `rewards.py` is the next PR.

## Pipeline (free, no LLM)

1. Fetch r/UAECreditCards posts (project Reddit connector during friends and family; swap to a company
   account at launch, only the fetcher changes). Read-only, never post.
2. `python scripts/reddit_intel.py --input posts.json` writes `data/cards/intel_candidates.json`: posts that
   mention a catalog card and an issue/bonus/combo pattern, each tagged `needs_review`.
3. A human promotes candidates into `intel.json`. Nothing reaches users unreviewed.
4. `GET /v1/cards/intel` returns active items matched to the user's cards (combos also show when only one
   half is held, with `missing_card_ids`). The app shows them as card warnings and tips.

## Open conflict

The only seeded item (SIB Cashback and Tabby) has two accounts: relayed from r/UAECreditCards that it works
only via Apple/Google Pay, versus a kredit.ae blog saying wallets do not override the exclusion. It ships as
`reported` with the conflict listed.
