"""field users, field sessions

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "field_users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("moh_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("phone", sa.String(32)),
        sa.Column("province", sa.String(64)),
        sa.Column("district", sa.String(64)),
        sa.Column("facility", sa.String(128)),
        sa.Column("role", sa.String(32), nullable=False, server_default="clinician"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("country_id", "moh_id", name="uq_field_users_country_moh"),
        sa.CheckConstraint("role IN ('clinician','supervisor')", name="ck_field_users_role"),
        sa.CheckConstraint("status IN ('pending','active','suspended')", name="ck_field_users_status"),
    )
    op.create_table(
        "field_sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "field_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("field_users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_field_sessions_user", "field_sessions", ["field_user_id"])


def downgrade() -> None:
    op.drop_index("ix_field_sessions_user", table_name="field_sessions")
    op.drop_table("field_sessions")
    op.drop_table("field_users")
