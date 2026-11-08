"""scans restaurant index, restaurant menus backfill

Revision ID: e5a9d3f1c7b4
Revises: c41d7a9e3b52
Create Date: 2026-11-08 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5a9d3f1c7b4"
down_revision: Union[str, Sequence[str], None] = "c41d7a9e3b52"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("idx_scans_restaurant", "scans", ["restaurant_id"])
    op.execute("""
        INSERT INTO restaurant_menu_items (restaurant_id, dish_id)
        SELECT DISTINCT s.restaurant_id, a.dish_id
        FROM analyses a JOIN scans s ON s.id = a.scan_id
        WHERE s.restaurant_id IS NOT NULL
        ON CONFLICT ON CONSTRAINT uq_restaurant_dish DO NOTHING
        """)
    op.execute("""
        UPDATE restaurants r SET checked_menu_count = (
            SELECT count(*) FROM restaurant_menu_items m WHERE m.restaurant_id = r.id
        )
        """)


def downgrade() -> None:
    op.drop_index("idx_scans_restaurant", table_name="scans")
