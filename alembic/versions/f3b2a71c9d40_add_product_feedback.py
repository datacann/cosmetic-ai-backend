"""add product feedback queue

Revision ID: f3b2a71c9d40
Revises: e91c5ab31d20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f3b2a71c9d40"
down_revision: Union[str, Sequence[str], None] = "e91c5ab31d20"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "product_feedback",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("analysis_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("details", sa.String(length=500), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False, server_default="pending"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_id"], ["analyses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "analysis_id", "reason", name="uq_product_feedback_reason"),
    )
    op.create_index("ix_product_feedback_user_id", "product_feedback", ["user_id"])
    op.create_index("ix_product_feedback_analysis_id", "product_feedback", ["analysis_id"])
    op.create_index("ix_product_feedback_product_id", "product_feedback", ["product_id"])
    op.create_index("ix_product_feedback_status", "product_feedback", ["status"])


def downgrade() -> None:
    op.drop_table("product_feedback")
