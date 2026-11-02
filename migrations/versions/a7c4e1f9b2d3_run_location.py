"""run location resolution

Revision ID: a7c4e1f9b2d3
Revises: b3f1c9d2a7e4
Create Date: 2026-11-02 09:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'a7c4e1f9b2d3'
down_revision: Union[str, Sequence[str], None] = 'b3f1c9d2a7e4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column('runs', sa.Column('location', postgresql.JSONB(astext_type=sa.Text()), nullable=True))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('runs', 'location')
