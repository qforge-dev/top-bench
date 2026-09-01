"""Cache invariant NAM baseline analysis per benchmark case.

Revision ID: 20260901_06
Revises: 20260901_05
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_06"
down_revision: str | None = "20260901_05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_cases",
        sa.Column(
            "nam_metrics_cache",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column(
        "benchmark_cases",
        sa.Column("nam_cache_signature", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_cases", "nam_cache_signature")
    op.drop_column("benchmark_cases", "nam_metrics_cache")
