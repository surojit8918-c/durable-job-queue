"""add_claim_query_optimized_index

Revision ID: 58f0f07fd4af
Revises: ad6923c685cc
Create Date: 2026-11-22 09:24:50.302884

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "58f0f07fd4af"
down_revision: str | Sequence[str] | None = "ad6923c685cc"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add partial index optimized for atomic claiming and priority FIFO queue ordering."""
    op.create_index(
        "ix_jobs_claim_opt",
        "jobs",
        [sa.text("priority DESC"), sa.text("created_at ASC")],
        unique=False,
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRYING')"),
    )


def downgrade() -> None:
    """Drop claim optimization index."""
    op.drop_index(
        "ix_jobs_claim_opt",
        table_name="jobs",
        postgresql_where=sa.text("status IN ('QUEUED', 'RETRYING')"),
    )
