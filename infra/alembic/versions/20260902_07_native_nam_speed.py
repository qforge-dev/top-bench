"""Store the machine-local native NAM-A2 speed calibration.

Revision ID: 20260902_07
Revises: 20260901_06
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260902_07"
down_revision: str | None = "20260901_06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("benchmark_runs", sa.Column("nam_a2_realtime_x", sa.Float(), nullable=True))
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "speed_calibration",
            sa.JSON(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "speed_calibration")
    op.drop_column("benchmark_runs", "nam_a2_realtime_x")
