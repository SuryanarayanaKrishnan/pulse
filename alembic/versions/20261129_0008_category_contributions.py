"""category contributions: anonymous weekly aggregates behind the shared merchant map

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "category_contributions",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("week", sa.Date(), nullable=False),
        sa.Column("merchant_key", sa.String(length=40), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("n", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("week", "merchant_key", "category", name="uq_category_contrib"),
    )
    op.create_index("ix_category_contributions_week", "category_contributions", ["week"])


def downgrade() -> None:
    op.drop_index("ix_category_contributions_week", table_name="category_contributions")
    op.drop_table("category_contributions")
