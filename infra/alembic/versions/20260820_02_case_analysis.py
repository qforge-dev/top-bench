from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_02"
down_revision: str | None = "20260820_01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("run_cases", sa.Column("level_db", sa.Float(), nullable=True))
    op.add_column("run_cases", sa.Column("peak_db", sa.Float(), nullable=True))
    op.add_column("run_cases", sa.Column("correlation", sa.Float(), nullable=True))
    op.add_column(
        "run_cases",
        sa.Column(
            "analysis",
            sa.JSON(),
            server_default=sa.text(
                '\'{"version"\\:"top-arena-case-analysis-v1",'
                '"window_seconds"\\:0.1,"hop_seconds"\\:0.1,"points"\\:[]}\''
            ),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("run_cases", "analysis")
    op.drop_column("run_cases", "correlation")
    op.drop_column("run_cases", "peak_db")
    op.drop_column("run_cases", "level_db")
