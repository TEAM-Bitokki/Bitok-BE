"""ingredients name_ko unique

Revision ID: 1475139f56c1
Revises: aab076abfa39
Create Date: 2026-11-06 11:52:32.835192

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "1475139f56c1"
down_revision: Union[str, Sequence[str], None] = "aab076abfa39"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint("ingredients_name_ko_key", "ingredients", ["name_ko"])


def downgrade() -> None:
    op.drop_constraint("ingredients_name_ko_key", "ingredients", type_="unique")
