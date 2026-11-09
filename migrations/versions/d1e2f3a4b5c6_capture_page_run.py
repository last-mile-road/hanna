"""capture page extraction run

Revision ID: d1e2f3a4b5c6
Revises: 9a1c2e3f4b5d
Create Date: 2026-11-09

The admitted page is read by one extraction run. The page row keeps the link so
the capture can stream the front and back separately and later allocate the
HANNA number only after both runs are clear.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d1e2f3a4b5c6"
down_revision: Union[str, Sequence[str], None] = "9a1c2e3f4b5d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("capture_pages", sa.Column("run_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        op.f("fk_capture_pages_run_id_runs"),
        "capture_pages",
        "runs",
        ["run_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_capture_pages_run", "capture_pages", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_capture_pages_run", table_name="capture_pages")
    op.drop_constraint(
        op.f("fk_capture_pages_run_id_runs"), "capture_pages", type_="foreignkey"
    )
    op.drop_column("capture_pages", "run_id")
