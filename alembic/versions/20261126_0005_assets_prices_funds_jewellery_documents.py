"""assets live prices funds jewellery documents

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-24 00:32:50.662303
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0005'
down_revision: str | None = '0004'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("asset_kind", "assets", type_="check")
    op.create_check_constraint("asset_kind", "assets", "kind IN ('real_estate', 'fd', 'gold', 'stock', 'crypto', 'cash', 'vehicle', 'gold_jewellery', 'other')")
    op.create_table('fund_holdings',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.Enum('india_mf', 'manual', name='fund_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('scheme_code', sa.String(length=40), nullable=True),
    sa.Column('isin', sa.String(length=20), nullable=True),
    sa.Column('folio', sa.String(length=40), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='AED', nullable=False),
    sa.Column('units', sa.Numeric(precision=24, scale=6), server_default='0', nullable=False),
    sa.Column('avg_cost_per_unit', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('sip_amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('sip_day', sa.SmallInteger(), nullable=True),
    sa.Column('manual_nav', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('manual_nav_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fund_holdings_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fund_holdings'))
    )
    op.create_index('ix_fund_holdings_user_id', 'fund_holdings', ['user_id'], unique=False)
    op.create_table('asset_valuations',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('asset_id', sa.UUID(), nullable=False),
    sa.Column('valued_on', sa.Date(), nullable=False),
    sa.Column('value_minor', sa.BigInteger(), nullable=False),
    sa.Column('basis', sa.String(length=12), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_asset_valuations_asset_id_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_asset_valuations_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_asset_valuations')),
    sa.UniqueConstraint('asset_id', 'valued_on', name='uq_asset_valuations_asset_day')
    )
    op.create_table('documents',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=24), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('institution', sa.String(length=160), nullable=True),
    sa.Column('reference', sa.String(length=120), nullable=True),
    sa.Column('amount_minor', sa.BigInteger(), nullable=True),
    sa.Column('currency', sa.String(length=3), server_default='AED', nullable=False),
    sa.Column('asset_id', sa.UUID(), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('storage_key', sa.String(length=512), nullable=True),
    sa.Column('blob_size', sa.BigInteger(), nullable=True),
    sa.Column('blob_sha256', sa.String(length=64), nullable=True),
    sa.Column('blob_encrypted', sa.Boolean(), server_default='true', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('deleted_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_documents_asset_id_assets'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_documents_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_documents'))
    )
    op.create_index('ix_documents_user_id', 'documents', ['user_id'], unique=False)
    op.create_table('fund_transactions',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('holding_id', sa.UUID(), nullable=False),
    sa.Column('txn_date', sa.Date(), nullable=False),
    sa.Column('kind', sa.Enum('purchase', 'sip', 'redemption', 'switch_in', 'switch_out', 'dividend', 'other', name='fund_txn_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('units', sa.Numeric(precision=24, scale=6), nullable=False),
    sa.Column('nav', sa.Numeric(precision=20, scale=6), nullable=True),
    sa.Column('amount_minor', sa.BigInteger(), nullable=False),
    sa.Column('description', sa.String(length=300), nullable=True),
    sa.Column('source', sa.String(length=10), server_default='manual', nullable=False),
    sa.Column('dedupe_hash', sa.String(length=64), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['holding_id'], ['fund_holdings.id'], name=op.f('fk_fund_transactions_holding_id_fund_holdings'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_fund_transactions_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_fund_transactions')),
    sa.UniqueConstraint('holding_id', 'dedupe_hash', name='uq_fund_transactions_holding_hash')
    )
    op.create_index('ix_fund_transactions_user_id', 'fund_transactions', ['user_id'], unique=False)
    op.create_table('jewellery_items',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('asset_id', sa.UUID(), nullable=False),
    sa.Column('description', sa.String(length=240), nullable=False),
    sa.Column('weight_grams', sa.Numeric(precision=12, scale=3), nullable=False),
    sa.Column('karat', sa.SmallInteger(), nullable=True),
    sa.Column('purity', sa.Numeric(precision=5, scale=4), nullable=True),
    sa.Column('making_charges_minor', sa.BigInteger(), nullable=True),
    sa.Column('purchase_price_minor', sa.BigInteger(), nullable=True),
    sa.Column('purchase_date', sa.Date(), nullable=True),
    sa.Column('photo_keys', postgresql.ARRAY(sa.String(length=512)), server_default='{}', nullable=False),
    sa.Column('backup_blob', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['asset_id'], ['assets.id'], name=op.f('fk_jewellery_items_asset_id_assets'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_jewellery_items_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_jewellery_items')),
    sa.UniqueConstraint('asset_id', name='uq_jewellery_items_asset_id')
    )
    op.create_index('ix_jewellery_items_user_id', 'jewellery_items', ['user_id'], unique=False)
    op.create_table('document_reminders',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('document_id', sa.UUID(), nullable=False),
    sa.Column('kind', sa.String(length=12), nullable=False),
    sa.Column('label', sa.String(length=160), nullable=True),
    sa.Column('due_on', sa.Date(), nullable=False),
    sa.Column('remind_days_before', sa.SmallInteger(), server_default='30', nullable=False),
    sa.Column('done_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['document_id'], ['documents.id'], name=op.f('fk_document_reminders_document_id_documents'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_document_reminders_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_document_reminders'))
    )
    op.create_index('ix_document_reminders_user_due', 'document_reminders', ['user_id', 'due_on'], unique=False)
    op.execute("ALTER TABLE fund_holdings ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE asset_valuations ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE documents ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE fund_transactions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE jewellery_items ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_reminders ENABLE ROW LEVEL SECURITY")
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_index('ix_document_reminders_user_due', table_name='document_reminders')
    op.drop_table('document_reminders')
    op.drop_index('ix_jewellery_items_user_id', table_name='jewellery_items')
    op.drop_table('jewellery_items')
    op.drop_index('ix_fund_transactions_user_id', table_name='fund_transactions')
    op.drop_table('fund_transactions')
    op.drop_index('ix_documents_user_id', table_name='documents')
    op.drop_table('documents')
    op.drop_table('asset_valuations')
    op.drop_index('ix_fund_holdings_user_id', table_name='fund_holdings')
    op.drop_table('fund_holdings')
    op.execute("DELETE FROM assets WHERE kind = 'gold_jewellery'")
    op.drop_constraint("asset_kind", "assets", type_="check")
    op.create_check_constraint("asset_kind", "assets", "kind IN ('real_estate', 'fd', 'gold', 'stock', 'crypto', 'cash', 'vehicle', 'other')")
    # ### end Alembic commands ###
