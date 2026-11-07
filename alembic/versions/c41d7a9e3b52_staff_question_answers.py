"""staff question answers

Revision ID: c41d7a9e3b52
Revises: 8b1f6c3e2d70
Create Date: 2026-11-07 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c41d7a9e3b52"
down_revision: Union[str, Sequence[str], None] = "8b1f6c3e2d70"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("staff_questions", sa.Column("target", sa.String(80), nullable=True))
    op.add_column("staff_checks", sa.Column("target", sa.String(80), nullable=True))
    op.add_column("staff_questions", sa.Column("answer", sa.String(10), nullable=True))
    op.add_column(
        "staff_questions",
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "staff_questions", sa.Column("staff_check_id", sa.BigInteger(), nullable=True)
    )
    op.create_foreign_key(
        "staff_questions_staff_check_id_fkey",
        "staff_questions",
        "staff_checks",
        ["staff_check_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_question_answer",
        "staff_questions",
        "answer IS NULL OR answer IN ('yes','no','unsure')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_question_answer", "staff_questions", type_="check")
    op.drop_constraint(
        "staff_questions_staff_check_id_fkey", "staff_questions", type_="foreignkey"
    )
    op.drop_column("staff_checks", "target")
    for column in ("staff_check_id", "answered_at", "answer", "target"):
        op.drop_column("staff_questions", column)
