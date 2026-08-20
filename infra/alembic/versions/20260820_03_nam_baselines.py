from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_03"
down_revision: str | None = "20260820_02"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_cases",
        sa.Column(
            "reference_latency_samples",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "benchmark_cases",
        sa.Column("nam_reference_wet_key", sa.String(length=1024), nullable=True),
    )
    for name in (
        "nam_esr",
        "nam_human_weighted_esr",
        "nam_mrstft",
        "nam_level_db",
        "nam_peak_db",
        "nam_correlation",
    ):
        op.add_column("run_cases", sa.Column(name, sa.Float(), nullable=True))


def downgrade() -> None:
    for name in (
        "nam_correlation",
        "nam_peak_db",
        "nam_level_db",
        "nam_mrstft",
        "nam_human_weighted_esr",
        "nam_esr",
    ):
        op.drop_column("run_cases", name)
    op.drop_column("benchmark_cases", "nam_reference_wet_key")
    op.drop_column("benchmark_cases", "reference_latency_samples")
