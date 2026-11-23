# Pulse API

Backend for a UAE personal-finance app: budgeting automation, card suggestions, asset tracking
(loans later). FastAPI + async SQLAlchemy 2 + Postgres (Supabase) + arq/Redis.

## Architecture

```
app/
  main.py              app factory: CORS, request-id middleware, error handlers, /health, /ready, /v1
  core/                config (pydantic-settings), db (async engine), security (Supabase JWT),
                       crypto (Fernet), errors (envelope), middleware, logging (structlog), queue (arq)
  api/deps.py          SessionDep, CurrentUser (verifies JWT, lazily provisions the users row)
  api/v1/routers/      me, accounts, transactions, budgets (+categories), assets (+liabilities, net-worth),
                       splits, ingest, health
  models/              SQLAlchemy models (one module per domain)
  schemas/             Pydantic request/response models
  services/            pure logic: splits (shares, balances, debt simplification), networth,
                       pagination, audit, ingest, plus the automatic-capture pieces:
    institutions.py      registry of UAE institutions (aliases, SMS senders, email domains, text markers)
    merchants.py         merchant normalisation + curated UAE merchant dictionary
    categorisation.py    user merchant rules > dictionary -> category (all ingest paths)
    parsers/             SMS: Parser protocol + registry, sms/<bank>.py templates, generic fallback;
                         statements: StatementParser protocol, statements/<bank>.py layouts, layout.py
                         (line parser), tables.py (table/CSV heuristic)
    statements/          documents (pikepdf/pdfplumber/CSV), intake, processing pipeline, matching
                         (rapidfuzz), reconcile, report
    gmail/               oauth (plain httpx), client (REST), sync (search + import)
    storage/             Storage protocol: local disk (dev) / S3-compatible (Supabase Storage)
  workers/             arq WorkerSettings + tasks (process_ingest, process_statement,
                       sync_gmail_connection, cron fan-out sync_all_gmail_connections)
alembic/               migrations (0001 = full initial schema)
tests/                 unit + API tests (no network/DB); tests/integration needs Postgres
```

Key decisions:

- **Auth**: Supabase issues JWTs; we only verify them. HS256 via `SUPABASE_JWT_SECRET`, asymmetric
  (RS256/ES256/EdDSA) via `SUPABASE_JWKS_URL`. The algorithm is allow-listed by what is configured, so a
  token cannot pick its own verification method. `users.id` is the Supabase `sub`; the row is created on
  first request. There is deliberately no FK to `auth.users` so local Postgres works.
- **Money**: integer minor units (`amount_minor`, fils) plus a 3-letter `currency` (default `AED`).
  Transaction amounts are always positive; `direction` (`debit`/`credit`) carries the sign.
- **Errors**: every error is `{"error": {"code", "message", "details", "request_id"}}`.
- **Request ID**: `X-Request-ID` is accepted/generated, bound to logs and echoed back.
- **Tenancy**: every query is scoped by `user_id`; foreign ids return 404, not 403. Split groups are
  visible to members only. Soft deletes (`deleted_at`) on user-owned data.
- **Row Level Security**: the migration enables RLS (no policies) on every table so Supabase's
  PostgREST/anon key cannot read them. The API connects as the table owner and bypasses RLS.
- **Dedupe**: `transactions` and `raw_ingests` are unique on `(user_id, external_hash)`.
- **Transaction pagination**: keyset cursor on `(posted_at desc, id desc)`, backed by the
  `(user_id, posted_at)` index.
- **Splits**: equal (remainder fils to the first participants), exact (must sum), percent
  (largest-remainder, must total 100). Balances = paid - owed +/- settlements; debt simplification is
  greedy (at most n-1 transfers; the true minimum is NP-hard). Groups are single-currency. Members may be
  people without accounts (`user_id` null).
- **Ingestion**: `POST /v1/ingest/{sms,notification,apple-shortcut}` stores the raw payload (deduped),
  enqueues `process_ingest`, returns 202 (200 + `duplicate: true` on repeat). The worker runs structured
  fields or the parser registry, resolves the account (explicit `account_id`, else unique `last4`
  [+ institution]), and creates a transaction. Outcomes: `parsed`, `needs_review` (account unknown),
  `skipped` (OTP/promo), `failed`.
- **SMS parsers**: `app/services/parsers/base.py` defines the `Parser` protocol (`institution`, `matches`,
  `parse`). Each of the 12 supported institutions has `parsers/sms/<bank>.py`: a list of regex templates with
  `{AMT}`/`{L4}`/`{M}`/`{DATE}` placeholders on top of shared common templates. `ParserRegistry.resolve` picks
  by explicit institution (free-text names such as "Emirates NBD" are accepted), then SMS sender ID, then
  bank name in the body; a bank parser that does not recognise a message falls through to `GenericParser`
  (OTPs are never parsed). The recognised institution helps resolve the account (unique `last4` per institution).
- **Encryption**: `app/core/crypto.py` wraps `MultiFernet` (comma-separated `FERNET_KEYS`, first key
  encrypts, all decrypt, `rotate()` re-encrypts). Used for `email_connections.encrypted_refresh_token` and
  `statement_passwords.encrypted_password`; neither is ever returned by an endpoint.

## Automatic capture (step 2)

**Merchants and categories.** `normalize_merchant` strips POS/gateway prefixes and trailing city/country/ref
numbers, then matches the dictionary in `services/merchants.py` (Carrefour, Lulu, Talabat, Deliveroo, Careem,
Noon, Amazon.ae, ENOC, ADNOC, DEWA, SEWA, e&, du, Salik, RTA, Tabby, Tamara, ...). Add a merchant with one
`_e(...)` line. `CategoryResolver` applies, in order: the user's `merchant_rules` (key = normalised merchant),
then the dictionary category. It runs for SMS/notification/shortcut ingest, statement lines and manual entry.
Setting a category on a transaction (`PATCH /v1/transactions/{id}`) upserts a rule ("always categorise this
merchant as X"; `remember_merchant=false` opts out, `apply_to_existing=true` also re-categorises history);
rules are also managed at `/v1/merchant-rules`.

**Statements.** `POST /v1/statements/upload` (multipart PDF/CSV, optional `institution`, `account_id`) stores
the file (`Storage`), creates a `statements` row (`pending`, deduped by SHA-256) and enqueues `process_statement`:

1. PDFs are decrypted with pikepdf using the user's stored password for the institution (all stored passwords
   if the institution is not known yet). No/invalid password => `parse_status=needs_password` and
   `action_required=set_statement_password`; setting the password (`POST|PUT /v1/statement-passwords`) re-queues
   the locked statements automatically. Passwords are Fernet-encrypted and write-only.
2. pdfplumber extracts text and ruled tables. `parse_document` tries the institution's parser (`parsers/statements/<bank>.py`,
   a thin `Layout` config over the line parser in `layout.py`), a bank-agnostic layout, and the header-driven
   table/CSV heuristic (`tables.py`); the first candidate whose lines reconcile with the printed balances wins.
3. The account is resolved (explicit, else institution + last4, else the only account of that type), otherwise
   `needs_review` (`action_required=choose_account`; fix with `POST /v1/statements/{id}/reprocess {account_id}`).
4. Lines are stored in `statement_lines`; `opening + sum(lines)` is compared with the closing balance
   (`reconciliation_status` matched/mismatch, `diff_minor`). Balance sign convention: cards = amount owed
   (closing = opening + purchases - payments), accounts = funds held (closing = opening + credits - debits).
5. Each line is matched against already captured SMS/notification/shortcut/manual transactions of the same account
   (same direction and amount, or the foreign amount an SMS reported; within 3 days; merchant similarity via
   rapidfuzz; greedy one-to-one). Matches are *linked* (`transactions.statement_id`) instead of duplicated; the rest
   are created with `source=statement` and a stable `external_hash`, so overlapping statements and reprocessing
   never duplicate. An SMS that arrives after its statement links to the statement's transaction.
   `GET /v1/statements/{id}/reconciliation` returns balances, the linked pairs, statement-only lines and
   captured-only transactions in the period.

**Gmail.** `POST /v1/email-connections/gmail/start` returns the Google consent URL (scope `gmail.readonly`,
offline, signed short-lived `state`). Google redirects the browser to `GET /v1/email-connections/gmail/callback`
(unauthenticated; the state identifies the user), which stores the Fernet-encrypted refresh token and queues a
sync. `sync_gmail_connection` searches each institution's sender domains/subject terms (`services/institutions.py`)
since `last_synced_at` (minus a small overlap), downloads PDF attachments (sender address is re-checked), stores
them and creates statements idempotently via `external_id = gmail:<message id>:<attachment index>` plus the
content hash. A cron (`sync_all_gmail_connections`, every 6 hours) fans out one job per active connection. A
revoked token marks the connection `needs_reauth`; partial failures leave `last_synced_at` unchanged.

**Storage.** `STORAGE_BACKEND=local` (dev, `STORAGE_LOCAL_DIR`) or `s3` (Supabase Storage's S3-compatible endpoint:
`S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`). Keys are `statements/<user id>/<uuid>.<ext>`;
the bucket must be private.

**Adjusting parsers.** Statement layouts and SMS wording were written against documented generic layouts, not real
statements (see "Known limitations"). To adapt a bank: edit its module (`Layout` flags such as `use_second_date`,
`foreign_before_local`, `summary_table`, label regexes) or add an SMS template, and add the sample to the tests.
- Product analytics are not stored here (PostHog); `audit_log` is for security-relevant actions.

## API surface (`/v1`)

| Area | Endpoints |
|------|-----------|
| me | `GET/PATCH/DELETE /me` |
| accounts | `GET/POST /accounts`, `GET/PATCH/DELETE /accounts/{id}` |
| transactions | `GET /transactions` (filters: account, category, direction, source, date range, amount range, `q`; `cursor`, `limit`), `POST`, `GET/PATCH/DELETE /{id}` |
| budgets | `GET/POST/PUT /budgets`, `GET /budgets/overview`, `PATCH/DELETE /budgets/{id}` |
| assets | `/assets`, `/liabilities` CRUD, `GET /net-worth?usd_rate=` |
| splits | `/splits/groups` (+ `/members`, `/expenses`, `/settlements`, `GET /balances`), Aani: `POST /splits/groups/{id}/settlements/aani-request`, `POST .../settlements/{sid}/mark-paid`, `POST .../settlements/{sid}/confirm`, `DELETE .../settlements/{sid}`, `GET /splits/settlements/awaiting-confirmation` |
| ingest | `POST /ingest/sms`, `/ingest/notification`, `/ingest/apple-shortcut` |
| statements | `POST /statements/upload`, `GET /statements`, `GET/DELETE /statements/{id}`, `POST /statements/{id}/reprocess`, `GET /statements/{id}/reconciliation` |
| statement passwords | `POST/PUT/GET /statement-passwords`, `DELETE /statement-passwords/{institution}` (password never returned) |
| email connections | `POST /email-connections/gmail/start`, `GET /email-connections/gmail/callback`, `GET /email-connections`, `POST /email-connections/{id}/sync`, `DELETE /email-connections/{id}` |
| categorisation | `GET/POST /merchant-rules`, `DELETE /merchant-rules/{id}` (the learnt "this merchant" shortcut; see Rules) |
| categories | `GET/POST /categories`, `PATCH/DELETE /categories/{id}` |
| rules | `GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`, `POST /rules/preview`, `POST /rules/reorder`, `POST /rules/{id}/apply` |
| salary | `GET /salary`, `POST /salary/detect`, `DELETE /salary/records/{id}` |
| contacts | `POST /contacts/match` |
| ops | `GET /health` (liveness), `GET /ready` (DB + Redis) |

OpenAPI docs are at `/docs` (disabled when `APP_ENV=production`).

## Budgeting, rules, salary, contacts and Aani (migration 0003)

All bodies are JSON, amounts are integer fils, errors use the standard envelope. Auth is the usual bearer JWT.

### Categories
The default set is shared by every user (`user_id: null`, `is_default: true`) and has stable `slug`s:
`salary, other_income (income)`, `groceries, dining ("Dining & Delivery"), transport ("Transport & Fuel"), car_loan,
home_loan, rent ("Rent & Housing"), utilities, telecom, fixed_expenses, education ("Education Fees"), health,
shopping, travel, entertainment, family ("Family & Remittances"), savings, fees, other (expense)`,
`cc_payment ("Credit Card Payments"), transfer (transfer; excluded from spend)`. Each has `kind`, `is_fixed`
(fixed vs variable), `icon` and `color` keys (the app maps them to glyphs/palette) and `sort_order`.
The migration renamed the 17 matching legacy defaults in place (ids and all FK data kept) and merged the old
"Fuel" into "Transport & Fuel" (transactions, rules and budgets re-pointed; same-month budgets summed).

- `GET /categories?include_hidden=false&kind=` returns defaults + the user's own; `name` is the effective name,
  `default_name` the original; sub-categories carry `parent_id`.
- `POST /categories {name, kind?, parent_id?, icon?, color?, is_fixed?}`: custom category; one sub-level only
  (`category_depth_exceeded`); a sub-category inherits its parent's kind; names unique case-insensitively (409).
- `PATCH /categories/{id}`: customs take any field; defaults only `name` (null resets) and `is_hidden`
  (`default_category_locked` otherwise). Stored per user in `category_preferences`.
- `DELETE /categories/{id}?reassign_to=<id>`: own categories only; refuses while it has sub-categories.

### Budgets
`PUT /budgets {category_id, month, limit_minor, rollover}` upserts (what a budget screen calls); `POST` creates (409 on
duplicates); `PATCH /budgets/{id} {limit_minor?, rollover?}`. Responses add `rollover`, `rollover_in_minor`
(unspent carried in from last month, never negative, chained through consecutive rollover months),
`available_minor = limit + rollover_in`, `spent_minor`, `remaining_minor`. A parent category's spend includes its
sub-categories; transactions with `exclude_from_budget` never count. `GET /budgets/overview?month=&currency=` returns
every visible expense category with its budget (or null) and spend: this is the "default set seeded for every budget".

### Rules ("automations")
A rule = conditions (all must match, AND) + actions, `priority` (lower runs first), `is_active`.

```json
{"name": "Gift cards", "priority": 50,
 "conditions": [{"field":"merchant","op":"contains","value":"carrefour"},
                {"field":"amount","op":"between","min_minor":10000,"max_minor":20000},
                {"field":"direction","value":"debit"},
                {"field":"account","account_id":"<uuid>"},
                {"field":"source","value":"sms"},
                {"field":"description","op":"regex","value":"gift\\s*card"}],
 "category_id":"<uuid>", "rename_merchant":"Weekly shop", "mark_transfer":false,
 "exclude_from_budget":false, "tags":["home"], "apply_to_existing": false}
```
`merchant`/`description` ops: `contains|equals|regex` (case-insensitive; `merchant` checks the normalised name and
the raw descriptor, `description` the raw line and notes). `amount` ops: `equals|between|gte|lte` (fils). Regexes
are length-capped and nested quantifiers are rejected (`(a+)+`). Across matching rules the first one to set a field
wins; tags accumulate. `mark_transfer` = Transfer category (unless one is set) + excluded from budget. User rules
are evaluated before the merchant dictionary on every path (SMS/notification/shortcut, statement lines, manual).

- `GET/POST /rules`, `GET/PATCH/DELETE /rules/{id}`; `POST /rules/reorder {ids:[...]}` (all ids, highest priority first).
- `POST /rules/preview {conditions, category_id?, ..., only_uncategorised?, date_from?, limit?}` returns
  `{matched_count, scanned_count, truncated, items:[{transaction, category_would_change}]}`; changes nothing.
- `POST /rules/{id}/apply {only_uncategorised?, date_from?}` returns `{matched, updated}`.
- `merchant_rules` was extended, not duplicated: old rows became merchant-equals rules (priority 1000, behind authored
  ones); `/merchant-rules` still manages that shortcut and `PATCH /rules/{id}` with new conditions detaches it.
- **"Always do this?"**: `PATCH /transactions/{id} {category_id}` now returns the transaction plus `suggested_rule`
  (a ready `POST /rules` body with `matching_count`, or null when nothing useful to offer). Nothing is saved unless
  the app posts it (optionally with `apply_to_existing: true`). **Behaviour change:** `remember_merchant` now defaults
  to `false`; send `true` to save the merchant shortcut immediately. Transactions also expose `exclude_from_budget`,
  `tags`, `applied_rule_id` (editable: `exclude_from_budget`, `tags`).

### Salary
Credits whose descriptor or SMS says SALARY / PAYROLL / WPS (Wage Protection) / SAL CR, at least AED 500, and not an
advance/loan/reversal/refund/fee, create one `salary_records` row per transaction (idempotent; SMS-only credits are
filed under Salary). `GET /salary?months=12` returns `detected, currency, is_recurring` (2+ months), `latest`,
`latest_month_minor`, `previous_month_minor`, `change_minor`, `average_monthly_minor` (last 3 months), `typical_day`,
`next_expected_on`, `monthly`, `employers`, `records`. `POST /salary/detect` rescans history;
`DELETE /salary/records/{id}` dismisses a false positive for good.

### Phones, contacts and Aani
- `PATCH /me {phone, aani_phone, phone_discoverable}`: UAE shorthand is normalised to E.164 (`050 123 4567`,
  `0501234567`, `971501234567`, `00971...`, `+971 (0) 50...` all become `+971501234567`; other countries need `+cc`).
  `aani_phone` must be a UAE mobile and is where payees receive Aani; it falls back to `phone`. null/"" clears.
  One live account per number (409 `phone_in_use`; numbers are not OTP-verified yet).
- **Hash contract for the app:** normalise to E.164 *with* `+`, SHA-256 the UTF-8 string, lower-case hex.
  `POST /contacts/match {hashes:[<=1000 hex64>]}` returns `{matches:[{phone_hash, user_id, display_name}]}` for
  discoverable users (never yourself). Hashes are not stored or logged; raw contact books never reach the server.
  Phone numbers have little entropy, so hashing is not strong anonymity.
- Split members accept `phone` (stored E.164 + hash); a discoverable Pulse user with that number is linked
  automatically. Members return `phone`.
- **Settlements** have `method` (`aani|cash|bank_transfer|other`, default `other`) and `status` (`pending|confirmed`).
  Only confirmed settlements count in `GET .../balances`. `cash`/`bank_transfer`/`other` are recorded as confirmed;
  `aani` starts pending. Flow (Aani has no public API): `POST /splits/groups/{id}/settlements/aani-request
  {to_member_id, amount_minor, from_member_id?, note?}` -> `{settlement, amount_minor, amount_display, payee_display_name,
  payee_phone, share_text, instructions}` (422 `payee_phone_missing` / `aani_requires_aed`); the payer pays in the bank
  app's "pay by mobile number", then `POST .../settlements/{sid}/mark-paid` (idempotent); the payee
  `POST .../confirm` -> confirmed. `DELETE .../{sid}` cancels while pending. Settlement objects carry
  `can_mark_paid`, `can_confirm`, `can_cancel` for the caller; `GET /splits/settlements/awaiting-confirmation` lists what
  the caller must confirm. A member without an account is acted for by the settlement's creator.

## Wallet capture and card rewards (migration 0004)

### Wallet taps (Apple Pay / Google Pay / Samsung Wallet)
`POST /v1/ingest/token` mints a per-user `pls_...` token (stored hashed; rotate by re-POSTing, `DELETE` revokes).
A Shortcut / Tasker / Samsung routine then posts to `/v1/ingest/apple-shortcut`, `/google-pay` or
`/samsung-wallet` with `Authorization: Bearer pls_...` (or `?token=`). The token only reaches ingest endpoints.
Wallet taps are processed inline and the response carries the transaction and any rewards `nudge`.
The same purchase seen by SMS, a wallet tap and the statement is stored once: a different-channel match within
60 minutes and a compatible merchant is merged (`transactions.sources`), and a statement line locks the row.

### Rewards engine
`cards_catalog.reward_structure` is JSON: `unit` (cashback AED or points), `point_value_aed`, `base_rate`,
`rules` (category and/or merchant match, `rate`, `monthly_cap_units`), `excluded_categories`,
`min_monthly_spend_minor`, `fee_waiver_spend_minor`. A merchant rule beats a category rule beats the base rate;
caps are per rule per statement cycle (cycle from the linked account's `statement_day`, else calendar month).
The maths is pure and deterministic (`app/services/rewards.py`).

| Endpoint | Purpose |
|---|---|
| `GET /v1/cards/catalog` | Reference cards with `verified_on`, `source`, `data_status` |
| `GET/POST /v1/cards`, `DELETE /v1/cards/{id}` | The user's cards, linked to a credit-card account |
| `GET /v1/rewards/summary` | Per card: earned, cap used/remaining, min-spend and fee-waiver progress, cycle dates |
| `POST /v1/rewards/best-card` | Rank the user's cards for a category/merchant and amount, cap headroom included |

The seeded catalog (14 UAE cards) is illustrative and ships **unverified** (`verified_at` NULL, no source):
treat figures as best-effort until someone checks them against the issuer and sets `verified_at` and
`source_url`; the API reports `data_status: "unverified"` until then. The nudge (`near_cap` at 80%, `capped`
at 100%) suggests another of the user's cards only when it earns more. Push delivery is a stub
(`app/services/push.py`: `LoggingPush`; `FcmPush` is not implemented).

## Insights, recurring payments and goals (migration 0004)

All amounts are AED minor units. Everything below is deterministic arithmetic over the user's own data.

**Balances.** Accounts have no running balance of their own. A balance is an *anchor* (`balance_minor` on
create/PATCH of an account, or the latest parsed statement's closing balance) plus the transactions after it;
for a credit card the balance is the amount owed. An account with neither has an unknown balance and is left
out of totals (and called out in `notes`) rather than guessed.

| Endpoint | What it returns |
|---|---|
| `GET /v1/insights/cashflow` | 12 months of income, spend, saved and savings rate, plus a day-by-day projection of the liquid balance (recurring bills, loan EMIs, card statements, expected salary, and the 90-day average of other spending) |
| `GET /v1/insights/spend-trends` | Spend per category per month (sub-categories roll up), change of the last complete month vs the one before, top merchants |
| `GET /v1/cards/due-dates` | Per card: statement date, due date, `days_left`, minimum due and total due kept separate and net of payments since the statement, utilisation, pay-by nudges |
| `GET /v1/insights/net-worth-history` | Daily snapshots (assets incl. account balances, liabilities incl. card balances). Refreshed on read and by an arq cron at 00:30 Dubai; history starts at the first snapshot |
| `GET /v1/insights/safe-to-spend` | Liquid balance minus upcoming bills, loan EMIs, card statements and goal contributions until the next expected salary (else month end), with a breakdown |
| `GET /v1/insights/health-score` | 0-100 from savings rate (30), credit utilisation (25), on-time card payments (25), emergency runway (20) with component scores and a one-line summary; components without data are dropped and below 50% coverage there is no score |
| `GET /v1/recurring`, `/upcoming`, `PUT/DELETE /v1/recurring/{key}/review` | Detected recurring payments |
| `/v1/goals` | Savings goals with required monthly amount and progress |

Conventions worth knowing: income excludes transfers, card payments and any credit on a credit-card account;
spend excludes transfers, card payments, savings and `exclude_from_budget` transactions. Card spending is
counted when it happens; the card's statement balance is a separate due-date event, so nothing is counted twice
as long as bank-side card payments are categorised "Credit Card Payments". Total due is the statement's closing
balance; both dues are reduced by credits on the card since the statement date. Without a parsed statement the
due date falls back to the account's `due_day` with no amounts. Savings goals reserve their monthly amount,
prorated by days left until the horizon.

**Recurring detection** (`app/services/recurring.py`): a merchant with 3+ debits (2+ for yearly) whose gaps fit
weekly, fortnightly, monthly, quarterly or yearly. Amounts are fixed when within 5% of the median, otherwise
variable. Flags: `price_increase` (latest charge at least 3% and AED 1 above a stable history),
`forgotten_subscription` (known subscription merchant, 4+ charges, never confirmed), `lapsed` (expected date
long passed). A loan with an EMI and due date replaces the matching series. Limitations: AED only; merchant
grouping relies on normalised names; "forgotten" is a prompt to review, not a claim of non-use.

## Live valuations (migration 0004)

Gold, FX and a property index feed asset values; every source can fail and the app then shows the user's own
manual value. Nothing on the request path calls the network: a cron refreshes a `market_quotes` cache and
requests only read it.

* **Sources behind interfaces** (`app/services/valuations/providers.py`): `GoldProvider` (gold-api.com, USD per
  troy ounce), `FxProvider` (open.er-api.com, or the fixed AED/USD peg 3.6725 for USD only),
  `PropertyIndexProvider` (index levels from `PROPERTY_INDEX_JSON`; no free property API is wired in). The two
  HTTP endpoints are key-less free services written from their public docs and are **not exercised by the tests**
  (fakes and a mock transport are): confirm them against the live services before relying on them. Choose with
  `GOLD_PROVIDER` (`goldapi|none`), `FX_PROVIDER` (`open_er_api|peg|none`).
* **Cron** `refresh_valuations` (arq, 01/07/13/19:20 UTC): fetch quotes (one source failing never blocks the
  others, old quotes are kept), then revalue assets. `snapshot_net_worth` runs daily at 20:30 UTC.
* **Assets** opt in with `valuation_source`: `market` for gold (needs `quantity`, `unit` g/kg/oz/tola and
  `meta.purity` such as `22k` or `916`) and `estimate` for real estate (`meta.index`, plus `meta.base_value_minor`
  and `meta.base_date` or `cost_basis_minor` and `acquired_on`; value = base x latest index / index at base date).
  Gold is a **spot** value without making charges or dealer spread; property is an index estimate, not a valuation.
* Asset responses add `current_value_minor`, `value_basis` (`market|estimate|manual|cost_basis|unknown`),
  `last_updated` (age of the data behind the value) and `valuation_note` (how it was derived, or why it fell back).
  Automatic values older than 3 days fall back to the manual value (kept in `manual_value_minor`).
* `GET /v1/market/quotes`: gold per gram by purity, FX rates and property indices with `live|stale|unavailable`.
* `GET /v1/net-worth` now converts foreign currencies with the cached FX rates (USD at the peg when none is
  cached); an explicit `?usd_rate=` still wins. The response lists `fx_rates` used and `fx_last_updated`.

## Running locally

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env            # set SUPABASE_JWT_SECRET and FERNET_KEYS
make install
docker compose up -d postgres redis
make migrate
make dev                        # API on :8000
make worker                     # arq worker (separate terminal)
```

Or everything in containers: `make up` (api, worker, postgres, redis; migrations run first).

### Checks and tests

```bash
make check             # ruff + ruff format --check + mypy + pytest (no network/DB needed)
make test-integration  # also runs tests/integration against Postgres (TEST_DATABASE_URL)
```

Unit and API tests need no network or database (dependency overrides and fakes). Integration tests
apply the real migration to `TEST_DATABASE_URL` and drive the API end to end; they are skipped when it
is unset. CI runs both against a Postgres service container and also runs `alembic check` so models and
migrations cannot drift.

New migration: `make revision m="add foo"`, then review the generated file.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `APP_ENV` | `development` / `staging` / `production` (production hides docs) |
| `LOG_LEVEL`, `LOG_JSON` | structlog level; JSON output for production |
| `CORS_ORIGINS` | comma-separated allowed origins |
| `DATABASE_URL` | Supabase Postgres URL (`postgresql://` is rewritten to `+asyncpg`) |
| `DB_PGBOUNCER` | `true` for Supabase's transaction pooler (disables prepared-statement cache) |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | SQLAlchemy pool sizing |
| `REDIS_URL` | arq broker (`rediss://` for Upstash) |
| `SUPABASE_JWT_SECRET` | HS256 verification secret |
| `SUPABASE_JWKS_URL` | JWKS endpoint for asymmetric keys |
| `SUPABASE_JWT_AUDIENCE`, `SUPABASE_JWT_ISSUER` | claim checks (audience default `authenticated`) |
| `FERNET_KEYS` | comma-separated Fernet keys for token/password encryption |
| `STORAGE_BACKEND`, `STORAGE_LOCAL_DIR` | `local` or `s3`; local directory for dev |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_REGION`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY` | Supabase Storage S3 credentials |
| `MAX_UPLOAD_BYTES` | statement upload limit (default 15 MiB) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_REDIRECT_URI` | Google OAuth client (Gmail); the redirect URI must equal `<api>/v1/email-connections/gmail/callback` |
| `OAUTH_SUCCESS_REDIRECT`, `OAUTH_STATE_SECRET` | where to send the browser after consent; HMAC key for `state` (defaults to the first Fernet key) |
| `GMAIL_INITIAL_LOOKBACK_DAYS`, `GMAIL_SYNC_OVERLAP_DAYS`, `GMAIL_MAX_MESSAGES_PER_SYNC` | Gmail sync window and cap |

Generate a Fernet key: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`.
Losing the key makes stored tokens/passwords unrecoverable; back it up.

## Free-tier deployment notes

- **Database: Supabase free.** Use the *session pooler* or direct connection string for migrations
  (`alembic upgrade head`). For the running app prefer the *transaction pooler* (port 6543) and set
  `DB_PGBOUNCER=true`. Keep `DB_POOL_SIZE` small (3-5) given the free-tier connection limits. Free
  projects pause after a week of inactivity and have no point-in-time recovery, so take periodic
  `pg_dump` backups before inviting real users.
- **Redis: Upstash free.** Use the `rediss://` URL. arq polls Redis continuously, which burns Upstash's
  command quota; for the beta either accept that, raise `poll_delay` in `WorkerSettings`, or run the
  worker only when needed.
- **Web service: Render / Fly.io.** Build from the `Dockerfile`; the container honours `$PORT`. Set the
  health check path to `/health`. Run `alembic upgrade head` as a pre-deploy/release command
  (`fly.toml` `release_command`, or Render pre-deploy command). Free Render web services sleep after
  inactivity (cold starts of ~30-60s), which also delays SMS ingestion; Fly machines can auto-stop/start
  similarly. The worker is a second service with command `arq app.workers.settings.WorkerSettings`;
  Render's free tier has no free background worker, so for the beta either run the worker on Fly or add
  a small paid worker.
- Secrets (`SUPABASE_JWT_SECRET`, `FERNET_KEYS`, `DATABASE_URL`, `REDIS_URL`) belong in the platform's
  secret store, never in the repo.

## Known limitations

- **No real statements or SMS were available.** The 12 institution parsers (SMS templates and statement
  layouts), sender domains and subject terms are best-effort, based on documented generic layouts and typical
  wording; expect to adjust them per bank once real samples exist. Reconciliation is the safety net: a wrong
  parse shows up as `mismatch` with `diff_minor`.
- Statement parsing needs a text layer: scanned/image PDFs are not OCR'd (they end up `failed`).
- Gmail is exercised against a faked Google in tests; the real consent screen needs your Google Cloud OAuth
  client (the `gmail.readonly` scope is a restricted scope and requires Google's app verification for public use).
- Foreign-currency SMS only link to a statement line when the statement prints the original foreign amount.
- Statement files are stored as received (a password-protected PDF stays protected at rest); the bucket must be private.
- Ingest jobs/statements stuck in `pending` after a failed enqueue are not swept automatically yet; use
  `POST /statements/{id}/reprocess`.

## Not built yet

Card catalog/suggestion endpoints and loans endpoints (tables exist), Outlook/IMAP mail
connections, OCR for scanned statements, FX rates (net worth takes a manual `usd_rate`), a sweeper to
re-enqueue ingests/statements stuck in `pending`, rate limiting.
