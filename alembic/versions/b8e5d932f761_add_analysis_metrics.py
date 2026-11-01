"""add analysis metrics

Revision ID: b8e5d932f761
Revises: a7d4c821e650
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b8e5d932f761"
down_revision: Union[str, Sequence[str], None] = "a7d4c821e650"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "analysis_metrics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("analysis_id", sa.String(length=36), nullable=True),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("request_key", sa.String(length=120), nullable=False),
        sa.Column("flow_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("total_ms", sa.Integer(), nullable=False),
        sa.Column("stage_durations_ms", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("formula_cache_used", sa.Boolean(), nullable=False),
        sa.Column("ingredient_cache_used", sa.Boolean(), nullable=False),
        sa.Column("error_stage", sa.String(length=50), nullable=True),
        sa.Column("error_type", sa.String(length=120), nullable=True),
        sa.Column("model_usage", sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_analysis_metrics_analysis_id", "analysis_metrics", ["analysis_id"])
    op.create_index("ix_analysis_metrics_user_id", "analysis_metrics", ["user_id"])
    op.create_index("ix_analysis_metrics_request_key", "analysis_metrics", ["request_key"], unique=True)
    op.create_index("ix_analysis_metrics_flow_type", "analysis_metrics", ["flow_type"])
    op.create_index("ix_analysis_metrics_status", "analysis_metrics", ["status"])
    op.create_index("ix_analysis_metrics_created_at", "analysis_metrics", ["created_at"])


def downgrade() -> None:
    op.drop_table("analysis_metrics")
