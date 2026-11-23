"""wallet ingest tokens + dedupe, card rewards, insights snapshots, goals, valuations, households

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-25 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0004'
down_revision: str | None = '0003'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SOURCES_OLD = ("statement", "sms", "notification", "apple_shortcut", "manual")
SOURCES_NEW = SOURCES_OLD + ("google_wallet", "samsung_wallet")


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def _swap_source_checks(values: tuple[str, ...]) -> None:
    for table, name, col in (("transactions", "txn_source", "source"), ("raw_ingests", "ingest_channel", "channel")):
        op.drop_constraint(name, table, type_="check")
        op.create_check_constraint(name, table, f"{col} IN ({_in(values)})")


# --------------------------------------------------------------------------------------------
# Wallet ingest: tokens, multi-source dedupe
# --------------------------------------------------------------------------------------------
def _ingest_up() -> None:
    op.create_table('ingest_tokens',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('token_hash', sa.String(length=64), nullable=False),
    sa.Column('prefix', sa.String(length=12), nullable=False),
    sa.Column('last_used_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_ingest_tokens_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_ingest_tokens')),
    sa.UniqueConstraint('token_hash', name=op.f('uq_ingest_tokens_token_hash')),
    sa.UniqueConstraint('user_id', name='uq_ingest_tokens_user_id')
    )
    op.execute("ALTER TABLE ingest_tokens ENABLE ROW LEVEL SECURITY")
    _swap_source_checks(SOURCES_NEW)
    op.add_column('transactions', sa.Column('sources', postgresql.ARRAY(sa.String(length=20)), server_default=sa.text("'{}'::varchar[]"), nullable=False))
    op.add_column('transactions', sa.Column('statement_locked', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute("UPDATE transactions SET sources = ARRAY[source]::varchar[], statement_locked = (source = 'statement' OR statement_id IS NOT NULL)")


def _ingest_down() -> None:
    op.drop_column('transactions', 'statement_locked')
    op.drop_column('transactions', 'sources')
    op.execute("DELETE FROM raw_ingests WHERE channel IN ('google_wallet', 'samsung_wallet')")
    op.execute("DELETE FROM transactions WHERE source IN ('google_wallet', 'samsung_wallet')")
    _swap_source_checks(SOURCES_OLD)
    op.drop_table('ingest_tokens')


# --------------------------------------------------------------------------------------------
# Rewards: seeded UAE card catalog. Figures are ILLUSTRATIVE, taken from memory of public product
# pages, and ship UNVERIFIED (verified_at NULL) until someone checks them against the issuer's
# current terms and sets verified_at + source_url. The API exposes data_status accordingly.
# --------------------------------------------------------------------------------------------
EXCL = ["fees", "cc_payment", "transfer", "rent", "education", "utilities"]


def _card(issuer, name, network, fee, salary, unit, value, base, rules, **extra):
    structure = {"unit": unit, "point_value_aed": value, "base_rate": base, "rules": rules,
                 "excluded_categories": extra.pop("excluded", EXCL)}
    structure.update(extra)
    return {"issuer": issuer, "name": name, "network": network, "annual_fee_minor": fee,
            "min_salary_minor": salary, "reward_structure": structure, "source_url": extra.get("url")}


def _r(name, rate, categories=None, merchants=None, cap=None):
    rule = {"name": name, "rate": rate}
    if categories:
        rule["categories"] = categories
    if merchants:
        rule["merchants"] = merchants
    if cap is not None:
        rule["monthly_cap_units"] = cap
    return rule


UAE_CARDS = [
    _card("Emirates NBD", "Skywards Infinite", "visa", 0, 3000000, "points", 0.0357, 1.5,
          [_r("Travel and Emirates", 3, ["travel"], ["emirates"]), _r("Dining", 2, ["dining"])]),
    _card("Emirates NBD", "Go4it", "mastercard", 0, 500000, "points", 0.01, 1,
          [_r("Online shopping", 2, ["shopping"], cap=2000)]),
    _card("ADCB", "Lulu Platinum", "visa", 0, 500000, "points", 0.01, 1,
          [_r("Lulu", 5, None, ["lulu"], cap=1000), _r("Groceries", 2, ["groceries"], cap=1000)]),
    _card("ADCB", "Touchpoints Platinum", "visa", 0, 1000000, "points", 0.01, 1.5,
          [_r("Dining", 3, ["dining"], cap=3000), _r("Fuel", 2, ["transport"], cap=1500)]),
    _card("First Abu Dhabi Bank", "Cashback Card", "mastercard", 0, 800000, "cashback", 1, 0.01,
          [_r("Groceries", 0.05, ["groceries"], cap=100), _r("Dining", 0.03, ["dining"], cap=100)]),
    _card("Mashreq", "Cashback Credit Card", "visa", 0, 400000, "cashback", 1, 0.005,
          [_r("Dining", 0.05, ["dining"], cap=300), _r("Entertainment", 0.05, ["entertainment"], cap=300)]),
    _card("Mashreq", "Solitaire", "visa", 120000, 2500000, "points", 0.025, 1,
          [_r("Dining and travel", 2, ["dining", "travel"])], fee_waiver_spend_minor=12000000),
    _card("Mashreq", "Noon Card", "visa", 0, 500000, "cashback", 1, 0.005,
          [_r("Noon", 0.06, None, ["noon"], cap=250)]),
    _card("Dubai Islamic Bank", "Cashback Card", "visa", 0, 500000, "cashback", 1, 0.005,
          [_r("Groceries", 0.03, ["groceries"], cap=150), _r("Fuel", 0.02, ["transport"], cap=100)]),
    _card("RAKBANK", "Titanium Credit Card", "mastercard", 0, 500000, "cashback", 1, 0.01,
          [_r("Dining", 0.05, ["dining"], cap=150), _r("Groceries", 0.03, ["groceries"], cap=150)],
          min_monthly_spend_minor=200000),
    _card("HSBC", "Cashback Credit Card", "visa", 0, 800000, "cashback", 1, 0.01,
          [_r("Dining", 0.05, ["dining"], cap=100), _r("Groceries", 0.03, ["groceries"], cap=100)]),
    _card("Emirates Islamic", "Skywards Platinum", "visa", 0, 1500000, "points", 0.0357, 1,
          [_r("Dining", 2, ["dining"]), _r("Travel", 2, ["travel"])]),
    _card("Liv", "Liv Credit Card", "visa", 0, 0, "cashback", 1, 0.005,
          [_r("Dining", 0.03, ["dining"], cap=100), _r("Entertainment", 0.03, ["entertainment"], cap=100)]),
    _card("Standard Chartered", "Manhattan Rewards", "visa", 0, 1000000, "points", 0.01, 1,
          [_r("Dining", 2, ["dining"], cap=2000), _r("Groceries", 2, ["groceries"], cap=2000)]),
]


def _rewards_up() -> None:
    import json

    bind = op.get_bind()
    for card in UAE_CARDS:
        bind.execute(
            sa.text(
                "INSERT INTO cards_catalog (issuer, name, network, annual_fee_minor, min_salary_minor, "
                "reward_structure, perks, source_url, verified_at) VALUES (:issuer, :name, :network, "
                ":fee, :salary, CAST(:rs AS jsonb), '{}'::jsonb, NULL, NULL) "
                "ON CONFLICT (issuer, name) DO NOTHING"
            ),
            {"issuer": card["issuer"], "name": card["name"], "network": card["network"],
             "fee": card["annual_fee_minor"], "salary": card["min_salary_minor"],
             "rs": json.dumps({k: v for k, v in card["reward_structure"].items() if k != "url"})},
        )


def _rewards_down() -> None:
    bind = op.get_bind()
    for card in UAE_CARDS:
        bind.execute(
            sa.text("DELETE FROM cards_catalog WHERE issuer = :i AND name = :n AND verified_at IS NULL "
                    "AND id NOT IN (SELECT catalog_card_id FROM user_cards)"),
            {"i": card["issuer"], "n": card["name"]},
        )


def upgrade() -> None:
    _ingest_up()
    _rewards_up()


def downgrade() -> None:
    _rewards_down()
    _ingest_down()
