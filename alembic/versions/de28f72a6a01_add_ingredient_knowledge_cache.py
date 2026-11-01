"""add ingredient knowledge cache metadata

Revision ID: de28f72a6a01
Revises: b8e5d932f761
"""

from alembic import op
import sqlalchemy as sa


revision = "de28f72a6a01"
down_revision = "b8e5d932f761"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "ingredients",
        sa.Column("evidence_status", sa.String(length=30), nullable=False, server_default="unverified"),
    )
    op.add_column("ingredients", sa.Column("knowledge_source", sa.String(length=80), nullable=True))
    op.add_column("ingredients", sa.Column("source_urls", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("ingredients", sa.Column("knowledge_note", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("ingredients", "knowledge_note")
    op.drop_column("ingredients", "source_urls")
    op.drop_column("ingredients", "knowledge_source")
    op.drop_column("ingredients", "evidence_status")
