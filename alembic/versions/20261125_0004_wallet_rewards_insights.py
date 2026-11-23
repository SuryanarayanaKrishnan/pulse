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


def upgrade() -> None:
    _ingest_up()


def downgrade() -> None:
    _ingest_down()
