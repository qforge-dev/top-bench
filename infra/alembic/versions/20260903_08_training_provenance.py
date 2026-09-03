"""Store exact training positions and dry-file provenance.

Revision ID: 20260903_08
Revises: 20260902_07
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260903_08"
down_revision: str | None = "20260902_07"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "training_positions",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "training_dry_files",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "training_dry_files")
    op.drop_column("benchmark_runs", "training_positions")
