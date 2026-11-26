"""country messages

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-11-26
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "country_messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "author_account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "author_field_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_users.id", ondelete="SET NULL"),
        ),
        sa.Column("author_name", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("context_type", sa.String(32)),
        sa.Column("context_id", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index(
        "ix_country_messages_country",
        "country_messages",
        ["country_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_country_messages_country", table_name="country_messages")
    op.drop_table("country_messages")
