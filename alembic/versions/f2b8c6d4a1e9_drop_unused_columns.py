"""drop unused columns

Revision ID: f2b8c6d4a1e9
Revises: e5a9d3f1c7b4
Create Date: 2026-11-08 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f2b8c6d4a1e9"
down_revision: Union[str, Sequence[str], None] = "e5a9d3f1c7b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ck_severity", "dietary_profiles", type_="check")
    op.drop_column("dietary_profiles", "severity")
    op.drop_column("dietary_profiles", "cross_contamination")
    op.drop_constraint("uq_users_provider", "users", type_="unique")
    op.drop_column("users", "auth_provider")
    op.drop_column("users", "provider_uid")
    op.drop_column("analyses", "asked_tags")


def downgrade() -> None:
    op.add_column(
        "analyses",
        sa.Column(
            "asked_tags",
            postgresql.ARRAY(sa.String()),
            server_default="{}",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("provider_uid", sa.String(128), nullable=True))
    op.add_column("users", sa.Column("auth_provider", sa.String(20), nullable=True))
    op.create_unique_constraint(
        "uq_users_provider", "users", ["auth_provider", "provider_uid"]
    )
    op.add_column(
        "dietary_profiles",
        sa.Column(
            "cross_contamination",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
    )
    op.add_column(
        "dietary_profiles",
        sa.Column("severity", sa.String(20), server_default="normal", nullable=False),
    )
    op.create_check_constraint(
        "ck_severity", "dietary_profiles", "severity IN ('normal','severe')"
    )
