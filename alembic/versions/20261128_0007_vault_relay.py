"""vault relay: sealed emergency files released after a long silence

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "vault_relays",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("nominee_id", sa.String(length=64), nullable=False),
        sa.Column("sealed_file", sa.Text(), nullable=False),
        sa.Column("claim_hash", sa.String(length=64), nullable=False),
        sa.Column("interval_days", sa.Integer(), server_default=sa.text("90"), nullable=False),
        sa.Column("last_heartbeat_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("claim_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("warned_stage", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("claim_hash"),
        sa.UniqueConstraint("user_id", "nominee_id", name="uq_vault_relays_user_nominee"),
    )
    op.create_index("ix_vault_relays_user_id", "vault_relays", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_vault_relays_user_id", table_name="vault_relays")
    op.drop_table("vault_relays")
