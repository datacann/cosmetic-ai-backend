"""add daily usage limits

Revision ID: a7d4c821e650
Revises: f3b2a71c9d40
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "a7d4c821e650"
down_revision: Union[str, Sequence[str], None] = "f3b2a71c9d40"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("plan", sa.String(length=20), nullable=False, server_default="free"))
    op.create_table(
        "usage_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=True),
        sa.Column("guest_key", sa.String(length=64), nullable=True),
        sa.Column("request_key", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("charged", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_events_user_id", "usage_events", ["user_id"])
    op.create_index("ix_usage_events_guest_key", "usage_events", ["guest_key"])
    op.create_index("ix_usage_events_request_key", "usage_events", ["request_key"], unique=True)
    op.create_index("ix_usage_events_charged", "usage_events", ["charged"])
    op.create_index("ix_usage_events_status", "usage_events", ["status"])
    op.create_index("ix_usage_events_created_at", "usage_events", ["created_at"])


def downgrade() -> None:
    op.drop_table("usage_events")
    op.drop_column("users", "plan")
