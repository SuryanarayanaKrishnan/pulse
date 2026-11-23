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
                       pagination, merchants, audit, ingest, parsers/ (Parser protocol + registry)
  workers/             arq WorkerSettings + tasks
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
- **Parsers**: `app/services/parsers/base.py` defines the `Parser` protocol (`institution`,
  `matches`, `parse`). Register bank-specific parsers in `registry.py`; `GenericParser` is a regex
  placeholder fallback.
- **Encryption**: `app/core/crypto.py` wraps `MultiFernet` (comma-separated `FERNET_KEYS`, first key
  encrypts, all decrypt, `rotate()` re-encrypts). Used for `email_connections.encrypted_refresh_token` and
  `statement_passwords.encrypted_password` (models only; no endpoints yet).
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

## Not built yet

Statement upload/parsing and reconciliation endpoints, email connection flows, card catalog/suggestion
endpoints, salary records and loans endpoints (tables exist), real UAE bank parsers, FX rates (net worth
takes a manual `usd_rate`), a sweeper to re-enqueue ingests stuck in `pending`, rate limiting.
