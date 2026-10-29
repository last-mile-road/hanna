"""observation review mode and reviewed value

Revision ID: b3f1c9d2a7e4
Revises: 1c57a52b2b44
Create Date: 2026-10-29 16:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b3f1c9d2a7e4'
down_revision: Union[str, Sequence[str], None] = '1c57a52b2b44'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('observations', sa.Column('review_mode', sa.String(length=16), nullable=True))
    op.add_column('observations', sa.Column('reviewed_value', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('observations', 'reviewed_value')
    op.drop_column('observations', 'review_mode')
