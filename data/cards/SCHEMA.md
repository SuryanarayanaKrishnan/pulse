# UAE card catalog data (v1)

One JSON file per issuer in `data/cards/banks/<issuer-slug>.json`:

```json
{"issuer": "Emirates NBD", "cards": [ <card>, ... ]}
```

`scripts/build_card_catalog.py` validates every card and merges them into `data/cards/catalog.json`
(the file the API serves to phones, with `catalog_version`). Never edit `catalog.json` by hand.

## Card

| field | type | notes |
|---|---|---|
| `id` | str | stable slug, `<issuer>-<card>` e.g. `enbd-skywards-infinite`. Never reuse or rename. |
| `issuer`, `name` | str | `name` excludes the issuer. Matches `cards_catalog (issuer, name)`. |
| `network` | visa / mastercard / amex / unionpay / null | |
| `tier` | classic / gold / titanium / platinum / signature / infinite / world / world_elite / other | |
| `kind` | cashback / points / miles / cobrand / lifestyle / basic | |
| `islamic` | bool | |
| `status` | active / closed_to_new / discontinued / unverified | |
| `annual_fee_minor`, `joining_fee_minor` | int (fils, AED x 100) | 0 = free. null = unknown. |
| `fee_waiver` | `{spend_minor, period: "year", first_year_free: bool, text}` | |
| `min_salary_minor` | int/null | monthly AED fils |
| `fx_markup_pct` | number/null | |
| `reward_structure` | object | **exactly the shape of `app/services/rewards.py::parse_structure`**. Extra keys per rule are allowed and ignored by the engine: `conditions` (text), `channel` (`online`, `wallet`, `in_store`, `international`), `mcc_exclusions` (list), `annual_cap_units`. |
| `perks` | object | `lounge`, `cinema`, `golf`, `valet`, `dining`, `insurance` - free-form `{text, count_per_year?, conditions?}` |
| `welcome_offer` | object/null | `{text, value_aed_est?, min_spend_minor?, window_days?, requires_fee_payment?, valid_until?, source_url, verified_on}`. `valid_until` is the application/issue deadline. `source_url` (https, official page or terms, never a comparison site) must also appear in `sources`; `verified_on` is the day a person read the offer there. Without them the card must be `confidence: high`. Drop an offer you cannot confirm from the bank's own page. |
| `sources` | list | `{url, kind: bank_page / kfs / tc_pdf / aggregator / community, verified_on: YYYY-MM-DD}`; at least one |
| `confidence` | high / medium / low | high = cross-checked with a bank page or T&C PDF; medium = single secondary source or recent-but-uncorroborated; low = headline numbers only |
| `field_confidence` | `{field: verified/reported/community/unverified}` | optional. Fields weaker than the card's `confidence`, e.g. `annual_fee_minor` when only a comparison site states it. See docs/CARD_DATA_GUARDS.md. |
| `community_notes` | list of `{text, source, as_of}` | anything from forums/Reddit: kept separate from official data |
| `notes` | str | gotchas in our own words |
| `deals` | list | ticket or partner discounts (B1G1 cinema, 50% off) that are not a share of spend; kept out of `reward_structure` so they never dominate rankings |

Units: money is AED fils (minor). Rates are fractions per AED spent (`0.05` = 5%). For points/miles,
`point_value_aed` is our conservative redemption value of one point. Caps are per statement cycle
in reward units (AED for cashback, points otherwise). If a bank states a cap in AED spend, convert to units.
Do not paste bank prose; record facts in your own words.
