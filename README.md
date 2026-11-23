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
| budgets | `GET/POST /categories`, `GET/POST /budgets`, `PATCH/DELETE /budgets/{id}` (returns spent/remaining) |
| assets | `/assets`, `/liabilities` CRUD, `GET /net-worth?usd_rate=` |
| splits | `/splits/groups` (+ `/members`, `/expenses`, `/settlements`, `GET /balances`) |
| ingest | `POST /ingest/sms`, `/ingest/notification`, `/ingest/apple-shortcut` |
| statements | `POST /statements/upload`, `GET /statements`, `GET/DELETE /statements/{id}`, `POST /statements/{id}/reprocess`, `GET /statements/{id}/reconciliation` |
| statement passwords | `POST/PUT/GET /statement-passwords`, `DELETE /statement-passwords/{institution}` (password never returned) |
| email connections | `POST /email-connections/gmail/start`, `GET /email-connections/gmail/callback`, `GET /email-connections`, `POST /email-connections/{id}/sync`, `DELETE /email-connections/{id}` |
| categorisation | `GET/POST /merchant-rules`, `DELETE /merchant-rules/{id}` |
| ops | `GET /health` (liveness), `GET /ready` (DB + Redis) |

OpenAPI docs are at `/docs` (disabled when `APP_ENV=production`).

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

Card catalog/suggestion endpoints, salary records and loans endpoints (tables exist), Outlook/IMAP mail
connections, OCR for scanned statements, FX rates (net worth takes a manual `usd_rate`), a sweeper to
re-enqueue ingests/statements stuck in `pending`, rate limiting.
