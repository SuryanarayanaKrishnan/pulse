"""statement ingestion

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-23 15:03:12.798290
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0002'
down_revision: str | None = '0001'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


NEW_TABLES = ("merchant_rules", "statement_lines")
OLD_PARSE = "'pending', 'parsed', 'failed', 'needs_review', 'skipped'"
NEW_PARSE = OLD_PARSE + ", 'needs_password'"


def _swap_parse_status_check(values: str) -> None:
    op.drop_constraint("parse_status", "statements", type_="check")
    op.create_check_constraint("parse_status", "statements", f"parse_status IN ({values})")


def upgrade() -> None:
    op.create_table('merchant_rules',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('merchant_key', sa.String(length=160), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_merchant_rules_category_id_categories'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_merchant_rules_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_merchant_rules')),
    sa.UniqueConstraint('user_id', 'merchant_key', name='uq_merchant_rules_user_id_merchant_key')
    )
    op.create_table('statement_lines',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('statement_id', sa.UUID(), nullable=False),
    sa.Column('line_no', sa.SmallInteger(), nullable=False),
    sa.Column('posted_on', sa.Date(), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('currency', sa.String(length=3), server_default='AED', nullable=False),
    sa.Column('direction', sa.String(length=8), nullable=False),
    sa.Column('foreign_amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('foreign_currency', sa.String(length=3), nullable=True),
    sa.Column('balance_minor', sa.BigInteger(), nullable=True),
    sa.Column('transaction_id', sa.UUID(), nullable=True),
    sa.Column('match_status', sa.String(length=16), server_default='unmatched', nullable=False),
    sa.Column('match_score', sa.SmallInteger(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['statement_id'], ['statements.id'], name=op.f('fk_statement_lines_statement_id_statements'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['transaction_id'], ['transactions.id'], name=op.f('fk_statement_lines_transaction_id_transactions'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_statement_lines_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_statement_lines')),
    sa.UniqueConstraint('statement_id', 'line_no', name='uq_statement_lines_statement_id_line_no')
    )
    op.create_index('ix_statement_lines_transaction_id', 'statement_lines', ['transaction_id'], unique=False)
    op.add_column('email_connections', sa.Column('status', sa.String(length=16), server_default='active', nullable=False))
    op.add_column('email_connections', sa.Column('last_error', sa.Text(), nullable=True))
    op.create_index('uq_email_connections_user_provider_email', 'email_connections', ['user_id', 'provider', 'email'], unique=True, postgresql_where=sa.text('deleted_at IS NULL'))
    op.add_column('statements', sa.Column('institution', sa.String(length=40), nullable=True))
    op.add_column('statements', sa.Column('kind', sa.String(length=16), nullable=True))
    op.add_column('statements', sa.Column('source', sa.String(length=16), server_default='upload', nullable=False))
    op.add_column('statements', sa.Column('email_connection_id', sa.UUID(), nullable=True))
    op.add_column('statements', sa.Column('external_id', sa.String(length=255), nullable=True))
    op.add_column('statements', sa.Column('file_sha256', sa.String(length=64), nullable=True))
    op.add_column('statements', sa.Column('original_filename', sa.String(length=255), nullable=True))
    op.add_column('statements', sa.Column('content_type', sa.String(length=100), nullable=True))
    op.add_column('statements', sa.Column('parse_error', sa.Text(), nullable=True))
    op.add_column('statements', sa.Column('summary', postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.alter_column('statements', 'account_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.alter_column('statements', 'period_start',
               existing_type=sa.DATE(),
               nullable=True)
    op.alter_column('statements', 'period_end',
               existing_type=sa.DATE(),
               nullable=True)
    op.create_index('uq_statements_user_id_external_id', 'statements', ['user_id', 'external_id'], unique=True)
    op.create_index('uq_statements_user_id_file_sha256', 'statements', ['user_id', 'file_sha256'], unique=True, postgresql_where=sa.text('deleted_at IS NULL AND file_sha256 IS NOT NULL'))
    op.create_foreign_key(op.f('fk_statements_email_connection_id_email_connections'), 'statements', 'email_connections', ['email_connection_id'], ['id'], ondelete='SET NULL')
    _swap_parse_status_check(NEW_PARSE)
    for table in NEW_TABLES:  # same RLS posture as every other table (see migration 0001)
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")


def downgrade() -> None:
    # Statements that were never matched to an account/period cannot exist in the old schema.
    op.execute("DELETE FROM statements WHERE account_id IS NULL OR period_start IS NULL OR period_end IS NULL")
    op.execute("UPDATE statements SET parse_status = 'failed' WHERE parse_status = 'needs_password'")
    _swap_parse_status_check(OLD_PARSE)
    op.drop_constraint(op.f('fk_statements_email_connection_id_email_connections'), 'statements', type_='foreignkey')
    op.drop_index('uq_statements_user_id_file_sha256', table_name='statements', postgresql_where=sa.text('deleted_at IS NULL AND file_sha256 IS NOT NULL'))
    op.drop_index('uq_statements_user_id_external_id', table_name='statements')
    op.alter_column('statements', 'period_end',
               existing_type=sa.DATE(),
               nullable=False)
    op.alter_column('statements', 'period_start',
               existing_type=sa.DATE(),
               nullable=False)
    op.alter_column('statements', 'account_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.drop_column('statements', 'summary')
    op.drop_column('statements', 'parse_error')
    op.drop_column('statements', 'content_type')
    op.drop_column('statements', 'original_filename')
    op.drop_column('statements', 'file_sha256')
    op.drop_column('statements', 'external_id')
    op.drop_column('statements', 'email_connection_id')
    op.drop_column('statements', 'source')
    op.drop_column('statements', 'kind')
    op.drop_column('statements', 'institution')
    op.drop_index('uq_email_connections_user_provider_email', table_name='email_connections', postgresql_where=sa.text('deleted_at IS NULL'))
    op.drop_column('email_connections', 'last_error')
    op.drop_column('email_connections', 'status')
    op.drop_index('ix_statement_lines_transaction_id', table_name='statement_lines')
    op.drop_table('statement_lines')
    op.drop_table('merchant_rules')
