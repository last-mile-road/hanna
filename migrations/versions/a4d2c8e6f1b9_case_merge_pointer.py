"""case merge pointer: a re-scan of a known number joins the case it belongs to

Revision ID: a4d2c8e6f1b9
Revises: d1e2f3a4b5c6
Create Date: 2026-11-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a4d2c8e6f1b9"
down_revision: Union[str, Sequence[str], None] = "d1e2f3a4b5c6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("cases", sa.Column("merged_into", sa.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_cases_merged_into",
        "cases",
        "cases",
        ["merged_into"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_cases_merged_into", "cases", ["merged_into"])


def downgrade() -> None:
    op.drop_index("ix_cases_merged_into", table_name="cases")
    op.drop_constraint("fk_cases_merged_into", "cases", type_="foreignkey")
    op.drop_column("cases", "merged_into")
