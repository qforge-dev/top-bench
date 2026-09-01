"""Add compact leaderboard metrics.

Revision ID: 20260901_05
Revises: 20260831_04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260901_05"
down_revision: str | None = "20260831_04"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "benchmark_runs",
        sa.Column(
            "leaderboard_metrics",
            sa.JSON(),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
        UPDATE benchmark_runs
        SET leaderboard_metrics = json_build_object(
            'esr', json_build_object('mean', metrics->'esr'->'mean'),
            'human_weighted_esr', json_build_object(
                'mean', metrics->'human_weighted_esr'->'mean'
            ),
            'mrstft', json_build_object('mean', metrics->'mrstft'->'mean'),
            'realtime_x', json_build_object('mean', metrics->'realtime_x'->'mean'),
            'nam_a2_full', json_build_object(
                'esr', json_build_object(
                    'mean', metrics->'nam_a2_full'->'esr'->'mean'
                ),
                'human_weighted_esr', json_build_object(
                    'mean', metrics->'nam_a2_full'->'human_weighted_esr'->'mean'
                ),
                'mrstft', json_build_object(
                    'mean', metrics->'nam_a2_full'->'mrstft'->'mean'
                )
            )
        )
        """
        )
    else:
        op.execute(
            """
        UPDATE benchmark_runs
        SET leaderboard_metrics = json_object(
            'esr', json_object('mean', json_extract(metrics, '$.esr.mean')),
            'human_weighted_esr', json_object(
                'mean', json_extract(metrics, '$.human_weighted_esr.mean')
            ),
            'mrstft', json_object('mean', json_extract(metrics, '$.mrstft.mean')),
            'realtime_x', json_object(
                'mean', json_extract(metrics, '$.realtime_x.mean')
            ),
            'nam_a2_full', json_object(
                'esr', json_object(
                    'mean', json_extract(metrics, '$.nam_a2_full.esr.mean')
                ),
                'human_weighted_esr', json_object(
                    'mean', json_extract(
                        metrics,
                        '$.nam_a2_full.human_weighted_esr.mean'
                    )
                ),
                'mrstft', json_object(
                    'mean', json_extract(metrics, '$.nam_a2_full.mrstft.mean')
                )
            )
        )
        """
        )


def downgrade() -> None:
    op.drop_column("benchmark_runs", "leaderboard_metrics")
