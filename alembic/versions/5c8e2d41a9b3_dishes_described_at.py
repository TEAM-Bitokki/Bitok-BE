"""dishes described_at

Revision ID: 5c8e2d41a9b3
Revises: 1475139f56c1
Create Date: 2026-11-07 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "5c8e2d41a9b3"
down_revision: Union[str, Sequence[str], None] = "1475139f56c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dishes", sa.Column("described_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dishes", "described_at")
