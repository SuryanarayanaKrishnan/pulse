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


def upgrade() -> None:
    _categories_up()
    _budgets_up()


def downgrade() -> None:
    _budgets_down()
    _categories_down()
