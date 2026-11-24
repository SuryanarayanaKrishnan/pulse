"""Load the researched UAE card catalog (replaces the 14 illustrative seeds).

Data migration only: no schema change. Re-sync later with ``python -m app.services.card_catalog``
(``make sync-cards``) after data/cards/catalog.json changes.

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

from alembic import op

from app.services.card_catalog import load_catalog, sync_catalog_conn

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    sync_catalog_conn(op.get_bind(), load_catalog())


def downgrade() -> None:
    # Data only; the catalog is reference data and old rows may be held by users. Nothing to undo.
    pass
