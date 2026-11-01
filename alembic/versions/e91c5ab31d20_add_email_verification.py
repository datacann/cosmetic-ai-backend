"""add email verification

Revision ID: e91c5ab31d20
Revises: d43aa892ce51
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "e91c5ab31d20"
down_revision: Union[str, Sequence[str], None] = "d43aa892ce51"
branch_labels = None
depends_on = None

def upgrade() -> None:
    op.add_column("users", sa.Column("email_verified", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.add_column("users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True))
    op.create_table(
        "email_verification_tokens",
        sa.Column("id", sa.String(length=36), nullable=False), sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False), sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"), sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_verification_tokens_user_id", "email_verification_tokens", ["user_id"])
    op.create_index("ix_email_verification_tokens_token_hash", "email_verification_tokens", ["token_hash"], unique=True)
    op.create_index("ix_email_verification_tokens_expires_at", "email_verification_tokens", ["expires_at"])

def downgrade() -> None:
    op.drop_table("email_verification_tokens")
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "email_verified")
