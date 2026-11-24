"""default category set, budget rollover, classification rules, salary detection, contacts + Aani settlements

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-24 09:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# --------------------------------------------------------------------------------------------
# Default categories
# --------------------------------------------------------------------------------------------
# slug, name, kind, is_fixed, icon, color, sort_order
DEFAULTS = [
    ("salary", "Salary", "income", True, "briefcase", "emerald", 10),
    ("other_income", "Other Income", "income", False, "plus-circle", "lime", 20),
    ("groceries", "Groceries", "expense", False, "shopping-basket", "green", 30),
    ("dining", "Dining & Delivery", "expense", False, "utensils", "orange", 40),
    ("transport", "Transport & Fuel", "expense", False, "car", "sky", 50),
    ("car_loan", "Car Loan", "expense", True, "car-key", "blue", 60),
    ("home_loan", "Home Loan / Mortgage", "expense", True, "landmark", "slate", 70),
    ("rent", "Rent & Housing", "expense", True, "home", "indigo", 80),
    ("utilities", "Utilities", "expense", False, "zap", "yellow", 90),
    ("telecom", "Telecom & Internet", "expense", True, "wifi", "cyan", 100),
    ("fixed_expenses", "Fixed Expenses", "expense", True, "repeat", "violet", 110),
    ("education", "Education Fees", "expense", True, "graduation-cap", "blue", 120),
    ("health", "Health", "expense", False, "heart-pulse", "rose", 130),
    ("shopping", "Shopping", "expense", False, "shopping-bag", "pink", 140),
    ("travel", "Travel", "expense", False, "plane", "teal", 150),
    ("entertainment", "Entertainment", "expense", False, "film", "fuchsia", 160),
    ("family", "Family & Remittances", "expense", False, "users", "amber", 170),
    ("savings", "Savings & Investments", "expense", False, "piggy-bank", "emerald", 180),
    ("cc_payment", "Credit Card Payments", "transfer", False, "credit-card", "gray", 190),
    ("transfer", "Transfer", "transfer", False, "arrows-left-right", "gray", 200),
    ("fees", "Fees & Charges", "expense", False, "receipt", "red", 210),
    ("other", "Other", "expense", False, "ellipsis", "stone", 220),
]
# Existing 0001 system category -> slug (renamed in place so every FK keeps pointing at it).
RENAMES = {
    "Groceries": "groceries",
    "Dining": "dining",
    "Transport": "transport",
    "Utilities": "utilities",
    "Rent": "rent",
    "Shopping": "shopping",
    "Entertainment": "entertainment",
    "Health": "health",
    "Travel": "travel",
    "Education": "education",
    "Subscriptions": "fixed_expenses",
    "Fees & Charges": "fees",
    "Other": "other",
    "Salary": "salary",
    "Other Income": "other_income",
    "Transfer": "transfer",
    "Credit Card Payment": "cc_payment",
}
# slug -> the name the default had before this migration (for downgrade).
OLD_NAMES = {
    "dining": "Dining", "transport": "Transport", "rent": "Rent", "education": "Education",
    "fixed_expenses": "Subscriptions", "cc_payment": "Credit Card Payment",
}
NEW_SLUGS = ("car_loan", "home_loan", "telecom", "family", "savings")
BY_SLUG = {d[0]: d for d in DEFAULTS}


def _categories_up() -> None:
    op.add_column('categories', sa.Column('slug', sa.String(length=40), nullable=True))
    op.add_column('categories', sa.Column('is_fixed', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('categories', sa.Column('color', sa.String(length=20), nullable=True))
    op.add_column('categories', sa.Column('sort_order', sa.Integer(), server_default=sa.text('0'), nullable=False))
    op.create_index('uq_categories_system_slug', 'categories', ['slug'], unique=True, postgresql_where=sa.text('user_id IS NULL'))
    op.create_table('category_preferences',
    sa.Column('id', sa.UUID(), server_default=sa.text('gen_random_uuid()'), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category_id', sa.UUID(), nullable=False),
    sa.Column('display_name', sa.String(length=80), nullable=True),
    sa.Column('is_hidden', sa.Boolean(), server_default=sa.text('false'), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['category_id'], ['categories.id'], name=op.f('fk_category_preferences_category_id_categories'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], name=op.f('fk_category_preferences_user_id_users'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_category_preferences')),
    sa.UniqueConstraint('user_id', 'category_id', name='uq_category_preferences_user_id_category_id')
    )
    op.execute("ALTER TABLE category_preferences ENABLE ROW LEVEL SECURITY")

    conn = op.get_bind()
    # 1. Merge the old "Fuel" default into "Transport" (now "Transport & Fuel"): re-point every reference.
    fuel = conn.execute(sa.text("SELECT id FROM categories WHERE user_id IS NULL AND name = 'Fuel'")).scalar()
    transport = conn.execute(sa.text("SELECT id FROM categories WHERE user_id IS NULL AND name = 'Transport'")).scalar()
    if fuel is not None and transport is not None:
        p = {"fuel": fuel, "transport": transport}
        conn.execute(sa.text("UPDATE transactions SET category_id = :transport WHERE category_id = :fuel"), p)
        conn.execute(sa.text("UPDATE merchant_rules SET category_id = :transport WHERE category_id = :fuel"), p)
        conn.execute(sa.text("UPDATE categories SET parent_id = :transport WHERE parent_id = :fuel"), p)
        # budgets are unique per (user, category, month): fold same-month, same-currency limits together...
        conn.execute(sa.text(
            "UPDATE budgets t SET limit_minor = t.limit_minor + f.limit_minor FROM budgets f "
            "WHERE f.category_id = :fuel AND t.category_id = :transport AND f.user_id = t.user_id "
            "AND f.month = t.month AND f.currency = t.currency"), p)
        # ...drop the Fuel budgets that still collide (different currency), re-point the rest.
        conn.execute(sa.text(
            "DELETE FROM budgets f USING budgets t WHERE f.category_id = :fuel AND t.category_id = :transport "
            "AND f.user_id = t.user_id AND f.month = t.month"), p)
        conn.execute(sa.text("UPDATE budgets SET category_id = :transport WHERE category_id = :fuel"), p)
        conn.execute(sa.text("DELETE FROM categories WHERE id = :fuel"), p)
    elif fuel is not None:
        conn.execute(sa.text("UPDATE categories SET name = 'Transport' WHERE id = :fuel"), {"fuel": fuel})

    # 2. Rename / annotate the survivors in place (ids, and therefore FK data, are untouched).
    for old, slug in RENAMES.items():
        _, name, kind, fixed, icon, color, order = BY_SLUG[slug]
        conn.execute(sa.text(
            "UPDATE categories SET slug = :slug, name = :name, kind = :kind, is_fixed = :fixed, icon = :icon, "
            "color = :color, sort_order = :ord WHERE user_id IS NULL AND name = :old"),
            {"slug": slug, "name": name, "kind": kind, "fixed": fixed, "icon": icon, "color": color, "ord": order, "old": old})
    # 3. Insert the new defaults.
    for slug in NEW_SLUGS:
        _, name, kind, fixed, icon, color, order = BY_SLUG[slug]
        conn.execute(sa.text(
            "INSERT INTO categories (slug, name, kind, is_fixed, icon, color, sort_order) "
            "VALUES (:slug, :name, :kind, :fixed, :icon, :color, :ord)"),
            {"slug": slug, "name": name, "kind": kind, "fixed": fixed, "icon": icon, "color": color, "ord": order})


def _categories_down() -> None:
    conn = op.get_bind()
    for slug, old in OLD_NAMES.items():
        conn.execute(sa.text("UPDATE categories SET name = :old WHERE user_id IS NULL AND slug = :slug"), {"old": old, "slug": slug})
    # Recreate "Fuel" (transactions merged into Transport cannot be told apart again).
    conn.execute(sa.text("INSERT INTO categories (name, kind) VALUES ('Fuel', 'expense')"))
    # The new defaults disappear; their references fall back to NULL / are removed through the FKs.
    conn.execute(sa.text(
        "DELETE FROM categories WHERE user_id IS NULL AND slug IN ('car_loan', 'home_loan', 'telecom', 'family', 'savings')"))
    op.drop_table('category_preferences')
    op.drop_index('uq_categories_system_slug', table_name='categories', postgresql_where=sa.text('user_id IS NULL'))
    op.drop_column('categories', 'sort_order')
    op.drop_column('categories', 'color')
    op.drop_column('categories', 'is_fixed')
    op.drop_column('categories', 'slug')


# --------------------------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------------------------
def _budgets_up() -> None:
    op.add_column('budgets', sa.Column('rollover', sa.Boolean(), server_default=sa.text('false'), nullable=False))


def _budgets_down() -> None:
    op.drop_column('budgets', 'rollover')


# --------------------------------------------------------------------------------------------
# Classification rules: merchant_rules grows into general AND-ed condition rules
# --------------------------------------------------------------------------------------------
def _rules_up() -> None:
    op.add_column('merchant_rules', sa.Column('name', sa.String(length=120), nullable=True))
    op.add_column('merchant_rules', sa.Column('priority', sa.Integer(), server_default=sa.text('100'), nullable=False))
    op.add_column('merchant_rules', sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.add_column('merchant_rules', sa.Column('conditions', postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column('merchant_rules', sa.Column('rename_merchant', sa.String(length=160), nullable=True))
    op.add_column('merchant_rules', sa.Column('mark_transfer', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('merchant_rules', sa.Column('exclude_from_budget', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('merchant_rules', sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), server_default=sa.text("'{}'::varchar[]"), nullable=False))
    op.alter_column('merchant_rules', 'merchant_key', existing_type=sa.String(length=160), nullable=True)
    op.alter_column('merchant_rules', 'category_id', existing_type=sa.UUID(), nullable=True)
    op.create_index('ix_merchant_rules_user_id_priority', 'merchant_rules', ['user_id', 'priority'], unique=False)
    # Existing "always categorise merchant X as Y" rows become merchant-equals rules, behind authored ones.
    op.execute(
        "UPDATE merchant_rules SET priority = 1000, "
        "conditions = jsonb_build_array(jsonb_build_object('field', 'merchant', 'op', 'equals', 'value', merchant_key))"
    )
    op.add_column('transactions', sa.Column('exclude_from_budget', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('transactions', sa.Column('tags', postgresql.ARRAY(sa.String(length=40)), server_default=sa.text("'{}'::varchar[]"), nullable=False))
    op.add_column('transactions', sa.Column('applied_rule_id', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_transactions_applied_rule_id_merchant_rules'), 'transactions', 'merchant_rules', ['applied_rule_id'], ['id'], ondelete='SET NULL')


def _rules_down() -> None:
    op.drop_constraint(op.f('fk_transactions_applied_rule_id_merchant_rules'), 'transactions', type_='foreignkey')
    op.drop_column('transactions', 'applied_rule_id')
    op.drop_column('transactions', 'tags')
    op.drop_column('transactions', 'exclude_from_budget')
    # Rules that are not plain merchant->category shortcuts cannot be represented by the old shape.
    op.execute("DELETE FROM merchant_rules WHERE merchant_key IS NULL OR category_id IS NULL")
    op.drop_index('ix_merchant_rules_user_id_priority', table_name='merchant_rules')
    op.alter_column('merchant_rules', 'category_id', existing_type=sa.UUID(), nullable=False)
    op.alter_column('merchant_rules', 'merchant_key', existing_type=sa.String(length=160), nullable=False)
    for col in ('tags', 'exclude_from_budget', 'mark_transfer', 'rename_merchant', 'conditions', 'is_active', 'priority', 'name'):
        op.drop_column('merchant_rules', col)


# --------------------------------------------------------------------------------------------
# Salary detection
# --------------------------------------------------------------------------------------------
def _salary_up() -> None:
    op.add_column('salary_records', sa.Column('transaction_id', sa.UUID(), nullable=True))
    op.add_column('salary_records', sa.Column('matched_by', sa.String(length=20), nullable=True))
    op.create_foreign_key(op.f('fk_salary_records_transaction_id_transactions'), 'salary_records', 'transactions', ['transaction_id'], ['id'], ondelete='CASCADE')
    op.create_unique_constraint('uq_salary_records_transaction_id', 'salary_records', ['transaction_id'])


def _salary_down() -> None:
    op.drop_constraint('uq_salary_records_transaction_id', 'salary_records', type_='unique')
    op.drop_constraint(op.f('fk_salary_records_transaction_id_transactions'), 'salary_records', type_='foreignkey')
    op.drop_column('salary_records', 'matched_by')
    op.drop_column('salary_records', 'transaction_id')


# --------------------------------------------------------------------------------------------
# Contacts (phone hashes) and Aani settlements
# --------------------------------------------------------------------------------------------
def _splits_up() -> None:
    op.add_column('users', sa.Column('phone_e164', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('phone_hash', sa.String(length=64), nullable=True))
    op.add_column('users', sa.Column('aani_phone_e164', sa.String(length=16), nullable=True))
    op.add_column('users', sa.Column('phone_discoverable', sa.Boolean(), server_default=sa.text('true'), nullable=False))
    op.create_index('uq_users_phone_hash', 'users', ['phone_hash'], unique=True, postgresql_where=sa.text('phone_hash IS NOT NULL AND deleted_at IS NULL'))
    op.add_column('split_members', sa.Column('phone_e164', sa.String(length=16), nullable=True))
    op.add_column('split_members', sa.Column('phone_hash', sa.String(length=64), nullable=True))
    op.create_index('ix_split_members_phone_hash', 'split_members', ['phone_hash'], unique=False)
    op.add_column('settlements', sa.Column('method', sa.Enum('aani', 'cash', 'bank_transfer', 'other', name='settlement_method', native_enum=False, create_constraint=True, length=32), server_default='other', nullable=False))
    op.add_column('settlements', sa.Column('status', sa.Enum('pending', 'confirmed', name='settlement_status', native_enum=False, create_constraint=True, length=32), server_default='confirmed', nullable=False))
    op.add_column('settlements', sa.Column('marked_paid_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('settlements', sa.Column('marked_paid_by', sa.UUID(), nullable=True))
    op.add_column('settlements', sa.Column('confirmed_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('settlements', sa.Column('confirmed_by', sa.UUID(), nullable=True))
    op.create_foreign_key(op.f('fk_settlements_marked_paid_by_users'), 'settlements', 'users', ['marked_paid_by'], ['id'], ondelete='SET NULL')
    op.create_foreign_key(op.f('fk_settlements_confirmed_by_users'), 'settlements', 'users', ['confirmed_by'], ['id'], ondelete='SET NULL')
    # Settlements recorded before this migration were counted immediately: they are confirmed.
    op.execute("UPDATE settlements SET confirmed_at = created_at, confirmed_by = created_by")


def _splits_down() -> None:
    op.drop_constraint(op.f('fk_settlements_confirmed_by_users'), 'settlements', type_='foreignkey')
    op.drop_constraint(op.f('fk_settlements_marked_paid_by_users'), 'settlements', type_='foreignkey')
    # Unconfirmed settlements were never counted; do not let them start counting after a downgrade.
    op.execute("DELETE FROM settlements WHERE status = 'pending'")
    for col in ('confirmed_by', 'confirmed_at', 'marked_paid_by', 'marked_paid_at', 'status', 'method'):
        op.drop_column('settlements', col)
    op.drop_index('ix_split_members_phone_hash', table_name='split_members')
    op.drop_column('split_members', 'phone_hash')
    op.drop_column('split_members', 'phone_e164')
    op.drop_index('uq_users_phone_hash', table_name='users', postgresql_where=sa.text('phone_hash IS NOT NULL AND deleted_at IS NULL'))
    for col in ('phone_discoverable', 'aani_phone_e164', 'phone_hash', 'phone_e164'):
        op.drop_column('users', col)


def upgrade() -> None:
    _categories_up()
    _budgets_up()
    _rules_up()
    _salary_up()
    _splits_up()


def downgrade() -> None:
    _splits_down()
    _salary_down()
    _rules_down()
    _budgets_down()
    _categories_down()
