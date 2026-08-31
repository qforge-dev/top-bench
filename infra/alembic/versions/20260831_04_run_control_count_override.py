from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260831_04"
down_revision: str | None = "20260820_03"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_runs",
        sa.Column("amp_control_count_override", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "amp_control_count_override")
