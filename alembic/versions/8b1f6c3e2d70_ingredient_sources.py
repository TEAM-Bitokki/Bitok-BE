"""dish ingredient sources

Revision ID: 8b1f6c3e2d70
Revises: 5c8e2d41a9b3
Create Date: 2026-11-07 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8b1f6c3e2d70"
down_revision: Union[str, Sequence[str], None] = "5c8e2d41a9b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dish_ingredient_sources",
        sa.Column("dish_id", sa.BigInteger(), nullable=False),
        sa.Column("ingredient_id", sa.BigInteger(), nullable=False),
        sa.Column("source_id", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["dish_id"], ["dishes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["ingredient_id"], ["ingredients.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["source_id"], ["ingredients.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("dish_id", "ingredient_id", "source_id"),
    )


def downgrade() -> None:
    op.drop_table("dish_ingredient_sources")
