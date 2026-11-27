# Keeping card and combo data correct

Why this exists: combo copy and saving figures used to be typed by hand. The "Reddit stack" was described
as four cards each taking a spend category when the real mechanic is a chain, and its saving was
computed from the wrong structure. These guards make that class of mistake fail a test.

## The rules

1. **Every fact has a source and a confidence.** Card facts live in `data/cards/banks/*.json` with
   `sources` (`bank_page`, `kfs`, `tc_pdf` are official; `aggregator` and `community` are not) and a card
   `confidence`. Where one field is weaker than the card, list it in `field_confidence`
   (`verified | reported | community | unverified`). The build fails if a comparison site or forum is
   filed as official.
2. **Anything a combo or figure leans on is a claim** in `data/cards/claims.json`: statement, value,
   evidence (`official | aggregator | community | assumption`), confidence, source URL, a verbatim quote
   for verified claims, and the date checked. `verified` requires official evidence and a quote;
   community evidence can never be better than `community`. A claim with `catalog_ref` must equal the
   number in the catalog, so the two cannot drift apart.
3. **Combos with a figure are money flows** (`flow` in `data/cards/intel.json`): `spend` on a card,
   `repay` one card's bill with another. Rates, caps and fees come from the catalog or from claims.
   `app/services/combo_flow.py` produces the numbered steps, the monthly and yearly breakdown, and the
   saving. A combo with a `flow` may not carry `annual_saving_aed` or `saving_note`, and its `body` may not
   contain digits. Its confidence can be no stronger than its weakest claim (our own spending assumption
   is listed but does not weaken it).
4. **Steps we cannot prove are shown but not counted.** The Tabby chain's last step (paying SIB with
   another card) is listed as "not counted" with the reason.
5. **Worked examples are tests.** `tests/test_combo_flow.py` asserts the Tabby to SIB chain: 3,600 of SIB
   cashback, minus 360 Tabby repayment fee, minus 199 SIB annual fee, 3,041 a year.

## Staying correct (all free, GitHub Actions)

- `offer-watch` (daily) diffs every official page the catalog and the claims rely on, including PDFs by hash.
- `scripts/source_freshness.py` (same workflow) opens or refreshes one issue when a claim is more than
  60 days old, an official card source more than 120 days, an item is past `review_by`, or an official
  page could not be read.
- `docs/CARD_EVIDENCE.md` is generated for every card (tier A to D) and a test fails if it is stale.

## Workflow for a data change

Edit `banks/*.json` or `claims.json`, run `python scripts/build_card_catalog.py` and
`python scripts/card_evidence_report.py`, then `python scripts/export_app_combos.py <pulse-app>/assets/catalog/combos.json`
and `python scripts/sync_catalog.py` in the app repo. Never edit the app's `combos.json` by hand.

## Audit of 2026-11-27 (official pages re-read)

| Item | Finding |
|---|---|
| Reddit stack combo | Wrong mechanic and wrong figure in the app. Rebuilt as a flow: Tabby, repaid by SIB via wallet, SIB bill paid by another card (not counted). 3,041 a year, community confidence. |
| Tabby repayment fee (~1%) | Not on any Tabby page. Community report only, shown as such. |
| Tabby Plus price | Catalog said AED 59, Tabby's card page lists 49 a month. Corrected. |
| SIB annual fee AED 199 | Not on SIB's pages (defer to a key fact statement we could not read). Flagged `reported`. |
| SIB base rate (1%) and reduced tier (0.5%) | Not published on SIB's page. Flagged `unverified` / `reported`. SIB never mentions Tabby or card-bill payments. |
| Liv tiers (5,000 / 10,000) and 0.1% bills rate | Liv says only "up to 2%" and AED 750 cap. Flagged. Cashback+ fee is AED 700 on Liv's page, 735 in the catalog (VAT?). |
| Dubai First | Homepage says 6% cashback, catalog 5%. Rates unconfirmed, card set to low confidence. Annual fee AED 399 plus VAT = 418.95 confirmed by the July 2025 schedule of charges. |
| Wio card-bill cashback | Wio says up to 0.5%, up to 2,500 a month. The AED 5,000 spend condition and cap of 0.5% of limit are not on Wio's pages; the combo body no longer states them as fact. |
| Mashreq noon | Rate cut on 23 Jan 2027 and AED 1,000 cap from 7 Mar 2027 confirmed on Mashreq's page. Combo upgraded to verified; the duplicate "govt rate cut" item retired. |
| Mashreq 24x7, EI Amazon | Rates in the catalog match the official pages read (EI Amazon "up to" 6% / 3% on Amazon.ae). |
| ADCB Talabat, ENBD noon One | Official pages could not be read on 2026-11-27 (blocked or error page). Left as they were; stay in the watch list. |
