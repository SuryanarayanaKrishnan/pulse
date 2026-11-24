# Best card combos (from catalog v2026-11-24)

Headline figures come from `data/cards/catalog.json` (170 cards, 68 high / 71 medium / 31 low confidence).
"High" means cross-checked on the bank's own page; no official KFS or T&C PDF could be opened in this
environment, so caps and reduced-rate lists are often aggregator-only. Rates are peak rates, they are
usually capped and several need a minimum monthly spend. Treat this as a map for the rewards engine and
the app's nudges, not as advice. Community items are marked.

## Starting points by category (cashback cards, medium or high confidence)

| Category | Strong options | Catch |
|---|---|---|
| Online and wallet spend | SIB Cashback 10% | AED 300/month cap (about AED 3,000 spend). Utilities, telecom, government, supermarkets, education, fuel earn 0.5%. |
| Groceries | FAB Cashback 5%, Ajman ULTRACASH 5% (pick categories), Dubai First 5% | FAB needs AED 3,000 spend in the previous month. |
| Dining and delivery | ADCB Talabat 35% on Talabat orders, SIB 10% online, Mashreq 24x7 7% | Talabat rate needs AED 2,500/month and has a cap. |
| Fuel and transport | ADNOC Rewards (FAB), EI RTA, EI Switch Cashback 8%, ADCB 365 5% | Switch minimum spend rises to AED 4,000 on 10 Jan 2027. 365 needs AED 5,000/month. |
| Travel | ADCB Traveller, ENBD Voyager World Elite (portal), FAB Travel | High salary bars (AED 20k to 25k), portal-only rates. |
| Everything else | Ruya Cashback 2%, ADCB Shukran 1.5%, ADCB Traveller 1.5% | Check spend gates. |

## Combos worth modelling

1. **SIB Cashback + a grocery/fuel card.** Use SIB for anything online or in a wallet until the AED 300
   cap, and a card that does not penalise supermarkets (FAB Cashback, Ajman) for groceries and fuel.
   SIB earns only 0.5% on supermarkets and utilities, so this split matters.
2. **Spend-gate pairing.** Several cards earn nothing below a monthly threshold (FAB Cashback AED 3,000,
   ADCB 365 AED 5,000, DIB cashback AED 3,000 to 4,000, EI Cashback Plus AED 3,000, RAKBANK RED/Titanium/World
   AED 1,000/2,000/10,000). The engine already has `min_monthly_spend_minor`; nudges should say "AED X more
   this cycle unlocks Y" rather than recommend two gated cards at once.
3. **Fee-waiver cards.** Many fees waive at a yearly spend (SIB AED 199 waived at AED 10,000). Put baseline
   spend on the card whose waiver you are close to before the anniversary.
4. **Branded points.** SHARE (Majid Al Futtaim), Darna (Aldar), U by Emaar and Lulu cards pay best at
   their own group; value them only if the user already shops there. Point values for these are our
   estimates (see each card's notes).

## Gotchas (all from `data/cards/community.md`, sources linked there)

- **Tabby on the SIB 10% card (community, unverified):** repayments reportedly post as MCC 5999 and earn
  0.5%. Tabby may charge up to about 1% to repay by card, so the net can be negative. Check the posted MCC.
- Hotel restaurants post as hotels, petrol-station shop purchases as convenience, delivery apps sometimes
  with generic codes. Categories in the app are by merchant, banks pay by MCC; show both when they disagree.
- Wallet top-ups, BNPL repayments, rent, government and education payments are commonly excluded or reduced.
- Offers end: Marriott Bonvoy World Elite 200k bonus applies to cards issued 15 Sep to 30 Nov 2026, FAB Etihad
  Platinum welcome ends 30 Nov 2026, RAKBANK Titanium/World offers end 31 Dec 2026. The offer watcher flags these.
- Cinema B1G1 and ticket discounts are stored under `deals`, not as earn rates.

## What the engine cannot yet express (data keeps it in extra keys)

International/EEA tiers, online vs in-store channel, total-monthly-spend tiers (Liv, Duo, RAKBANK, HSBC
Live+, Cashback Plus), card-level caps across all rules (`monthly_cap_*` keys), weekend earn (Go4it).
Recommend a small `channel` and `spend_tier` extension to `parse_structure` as the next engine change.
