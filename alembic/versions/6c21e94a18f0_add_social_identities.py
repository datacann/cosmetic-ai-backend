"""add social identities

Revision ID: 6c21e94a18f0
Revises: 3d791ab8e4d2
"""
from alembic import op
import sqlalchemy as sa

revision = "6c21e94a18f0"
down_revision = "3d791ab8e4d2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "social_identities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("provider", sa.String(length=20), nullable=False),
        sa.Column("provider_subject", sa.String(length=255), nullable=False),
        sa.Column("provider_email", sa.String(length=320), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider", "provider_subject", name="uq_social_identity_provider_subject"),
        sa.UniqueConstraint("user_id", "provider", name="uq_social_identity_user_provider"),
    )
    op.create_index("ix_social_identities_user_id", "social_identities", ["user_id"])
    op.create_index("ix_social_identities_provider", "social_identities", ["provider"])


def downgrade() -> None:
    op.drop_index("ix_social_identities_provider", table_name="social_identities")
    op.drop_index("ix_social_identities_user_id", table_name="social_identities")
    op.drop_table("social_identities")
