"""add favorites and routine products

Revision ID: b710d6d79c31
Revises: a9094f5522e4
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "b710d6d79c31"
down_revision: Union[str, Sequence[str], None] = "a9094f5522e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("analyses", sa.Column("is_favorite", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_table(
        "routine_products",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("product_id", sa.String(length=36), nullable=False),
        sa.Column("routine_time", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "product_id", "routine_time", name="uq_routine_product_time"),
    )
    op.create_index("ix_routine_products_user_id", "routine_products", ["user_id"])
    op.create_index("ix_routine_products_product_id", "routine_products", ["product_id"])


def downgrade() -> None:
    op.drop_table("routine_products")
    op.drop_column("analyses", "is_favorite")
