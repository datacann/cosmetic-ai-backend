"""add manual routine products

Revision ID: 3d791ab8e4d2
Revises: f40d3e9a71b2
"""

from alembic import op
import sqlalchemy as sa


revision = "3d791ab8e4d2"
down_revision = "f40d3e9a71b2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("routine_products", "product_id", existing_type=sa.String(length=36), nullable=True)
    op.add_column("routine_products", sa.Column("manual_brand", sa.String(length=120), nullable=True))
    op.add_column("routine_products", sa.Column("manual_name", sa.String(length=200), nullable=True))
    op.add_column("routine_products", sa.Column("manual_variant", sa.String(length=160), nullable=True))
    op.add_column("routine_products", sa.Column("manual_category", sa.String(length=80), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM routine_products WHERE product_id IS NULL")
    op.drop_column("routine_products", "manual_category")
    op.drop_column("routine_products", "manual_variant")
    op.drop_column("routine_products", "manual_name")
    op.drop_column("routine_products", "manual_brand")
    op.alter_column("routine_products", "product_id", existing_type=sa.String(length=36), nullable=False)
