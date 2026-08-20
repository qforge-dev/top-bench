from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_01"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "amps",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("amp_type", sa.String(length=100), nullable=False),
        sa.Column("control_names", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_amps_amp_type", "amps", ["amp_type"])

    op.create_table(
        "benchmark_cases",
        sa.Column("id", sa.String(length=255), nullable=False),
        sa.Column("amp_id", sa.String(length=128), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("position_index", sa.Integer(), nullable=False),
        sa.Column("position_matrix", sa.JSON(), nullable=False),
        sa.Column("dry_key", sa.String(length=1024), nullable=False),
        sa.Column("dry_sha256", sa.String(length=64), nullable=False),
        sa.Column("reference_wet_key", sa.String(length=1024), nullable=False),
        sa.Column("duration_seconds", sa.Float(), nullable=False),
        sa.Column("sample_rate", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["amp_id"], ["amps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("amp_id", "chunk_index", "position_index"),
    )
    op.create_index("ix_benchmark_cases_amp_id", "benchmark_cases", ["amp_id"])

    op.create_table(
        "benchmark_runs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("creator", sa.String(length=255), nullable=False),
        sa.Column("amp_id", sa.String(length=128), nullable=False),
        sa.Column("unique_positions_used", sa.Integer(), nullable=False),
        sa.Column("audio_duration_sum", sa.Float(), nullable=False),
        sa.Column("turns", sa.Integer(), nullable=False),
        sa.Column("training_time", sa.Float(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameter_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("client_finished", sa.Boolean(), nullable=False),
        sa.Column("total_cases", sa.Integer(), nullable=False),
        sa.Column("completed_cases", sa.Integer(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["amp_id"], ["amps.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_benchmark_runs_amp_id", "benchmark_runs", ["amp_id"])
    op.create_index("ix_benchmark_runs_creator", "benchmark_runs", ["creator"])
    op.create_index("ix_benchmark_runs_name", "benchmark_runs", ["name"], unique=True)
    op.create_index("ix_benchmark_runs_status", "benchmark_runs", ["status"])

    op.create_table(
        "run_cases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("benchmark_case_id", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("candidate_wet_key", sa.String(length=1024), nullable=True),
        sa.Column("realtime_x", sa.Float(), nullable=True),
        sa.Column("esr", sa.Float(), nullable=True),
        sa.Column("human_weighted_esr", sa.Float(), nullable=True),
        sa.Column("mrstft", sa.Float(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("scored_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["benchmark_case_id"], ["benchmark_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["benchmark_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "benchmark_case_id"),
    )
    op.create_index("ix_run_cases_benchmark_case_id", "run_cases", ["benchmark_case_id"])
    op.create_index("ix_run_cases_run_id", "run_cases", ["run_id"])
    op.create_index("ix_run_cases_status", "run_cases", ["status"])

    op.create_table(
        "run_events",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("run_id", sa.String(length=36), nullable=False),
        sa.Column("benchmark_case_id", sa.String(length=255), nullable=True),
        sa.Column("kind", sa.String(length=100), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["benchmark_case_id"], ["benchmark_cases.id"]),
        sa.ForeignKeyConstraint(["run_id"], ["benchmark_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_run_events_benchmark_case_id", "run_events", ["benchmark_case_id"])
    op.create_index("ix_run_events_kind", "run_events", ["kind"])
    op.create_index("ix_run_events_occurred_at", "run_events", ["occurred_at"])
    op.create_index("ix_run_events_run_id", "run_events", ["run_id"])


def downgrade() -> None:
    op.drop_table("run_events")
    op.drop_table("run_cases")
    op.drop_table("benchmark_runs")
    op.drop_table("benchmark_cases")
    op.drop_table("amps")
