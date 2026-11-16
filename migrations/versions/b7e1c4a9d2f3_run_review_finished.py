"""run review completion marker

Revision ID: b7e1c4a9d2f3
Revises: a4d2c8e6f1b9
Create Date: 2026-11-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "b7e1c4a9d2f3"
down_revision: Union[str, Sequence[str], None] = "a4d2c8e6f1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "runs", sa.Column("review_finished_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("runs", "review_finished_at")
