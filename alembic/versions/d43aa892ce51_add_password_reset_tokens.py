"""add password reset tokens

Revision ID: d43aa892ce51
Revises: c82f3e14a907
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "d43aa892ce51"
down_revision: Union[str, Sequence[str], None] = "c82f3e14a907"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.create_table(
        "password_reset_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_password_reset_tokens_user_id", "password_reset_tokens", ["user_id"])
    op.create_index("ix_password_reset_tokens_token_hash", "password_reset_tokens", ["token_hash"], unique=True)
    op.create_index("ix_password_reset_tokens_expires_at", "password_reset_tokens", ["expires_at"])

def downgrade() -> None:
    op.drop_table("password_reset_tokens")
