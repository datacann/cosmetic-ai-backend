"""preserve analysis score precision

Revision ID: f40d3e9a71b2
Revises: de28f72a6a01
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "f40d3e9a71b2"
down_revision: str | None = "de28f72a6a01"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "analysis_formula_results",
        "raw_score",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="raw_score::double precision",
    )
    op.alter_column(
        "analysis_formula_results",
        "coverage",
        existing_type=sa.Integer(),
        type_=sa.Float(),
        existing_nullable=True,
        postgresql_using="coverage::double precision",
    )
    # Legacy scoring stored coverage as 0..100. Canonical orchestration stores
    # confidence as 0..1; preserve legacy meaning while unifying the scale.
    op.execute(
        "UPDATE analysis_formula_results "
        "SET coverage = coverage / 100.0 WHERE coverage > 1"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE analysis_formula_results "
        "SET coverage = coverage * 100.0 WHERE coverage BETWEEN 0 AND 1"
    )
    op.alter_column(
        "analysis_formula_results",
        "coverage",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="round(coverage)::integer",
    )
    op.alter_column(
        "analysis_formula_results",
        "raw_score",
        existing_type=sa.Float(),
        type_=sa.Integer(),
        existing_nullable=True,
        postgresql_using="round(raw_score)::integer",
    )
