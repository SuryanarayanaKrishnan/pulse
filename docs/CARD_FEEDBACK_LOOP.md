# Feedback loop: did the suggestion actually pay out?

Goal: every suggestion the app makes ("use card X at Talabat") is later checked against what really
happened, and those outcomes raise or lower how much we trust the catalog's numbers for that card and rule.
Works on-device first; only a tiny anonymous outcome record leaves the phone, and only with consent.

## 1. What is recorded (on the phone, encrypted with the rest of the local database)

| step | event | fields |
|---|---|---|
| suggestion | `suggestion_shown` | `suggestion_id`, `card_id`, `rule_name`, `merchant_key`, `category`, `expected_rate`, `expected_cap_left`, `catalog_version` |
| action | `suggestion_followed` | matched later by the capture pipeline (SMS, wallet tap or statement): same card, merchant within 90 minutes |
| evidence | `outcome_observed` | the cashback/points line for that transaction parsed from the monthly statement, or the user's answer |
| user | `outcome_reported` | one tap after the statement posts: Paid as expected / Less than expected / Nothing / Not sure |

Automatic evidence beats taps: the statement parser already reads the posted transaction, and most banks
show a rewards line or monthly total. Ask the user only when the parser cannot tell.

## 2. What leaves the phone (opt-in, anonymous)

```json
{"card_id":"adcb-talabat","rule":"Talabat orders","merchant_key":"talabat","mcc":"5812",
 "outcome":"less","observed_rate_bucket":"0.2-0.3","expected_rate":0.35,
 "month":"2026-12","catalog_version":"2026-11-24","install_pseudonym":"<random, rotated monthly>"}
```

No amounts, no names, no account or card numbers, no user id. Rate is bucketed. The pseudonym is only
used to stop one person voting many times and is dropped after aggregation. The consent screen says
exactly this. Everything else (transactions, assets, budgets) stays end-to-end encrypted and never reaches
the aggregation path.

## 3. How confidence moves (server side, per card and rule)

- Each (card, rule) holds a Beta distribution. Prior from the catalog's confidence: high Beta(8,1),
  medium Beta(3,2), low Beta(1,2). "As expected" counts as a success; "less" or "nothing" as a failure
  (weighted by how far off: nothing = 1, less = 0.5).
- Report weight: 1.0 when statement evidence backs it, 0.4 for a bare tap. One report per transaction.
- Nothing changes until at least 20 distinct pseudonyms have reported (k-anonymity floor).
- Output is a `reliability` number (posterior mean) and a label, written into the card's served data
  next to the catalog confidence, never over it:
  - `confirmed_by_users`: mean at least 0.85 with 30 or more reports. Adds a badge; it can lift a
    medium card's display, never turn it into "verified" (that needs an official source).
  - `mixed_reports`: 90% lower bound below 0.6. The rule's estimated value is multiplied by the mean in
    rankings, so a rule that mostly fails stops being recommended first.
  - `needs_reverification`: mean below 0.4 with 30 or more reports. Opens an item in the offer-watch queue
    (`offers_feed.json`) so the card is re-read against its official terms and the data is fixed or the
    rule is marked unreliable.

## 4. Where it plugs in

- Rewards engine: `estimated_value = rule_value * reliability` (default 1.0 when no data).
- Cap and MCC gotchas learned from reports ("hotel restaurants post as hotels") become `mcc_exclusions`
  suggestions for the catalog, reviewed by a human before they ship.
- Product analytics: suggestion shown, followed, confirmed rates per card, so we see which suggestions
  people trust.
- Catalog sync: reliability fields travel in the same `catalog.json` sync, so phones need no extra call.

## 5. Build order

1. On-device: `suggestion_shown` and statement-matched `outcome_observed` (no user taps yet, no upload).
2. Opt-in upload endpoint `POST /v1/feedback/outcomes` (batch, rate limited) and a daily aggregation job
   (fits the existing three Cloud Scheduler jobs by running inside the cron already used for valuations,
   not a fourth job).
3. Reliability fields in the catalog payload and the ranking multiplier.
4. The "did it pay out?" tap, and the re-verification queue.

Open point to confirm with surya: wording and default of the consent toggle (we recommend off by default,
asked once after the first statement is parsed).
