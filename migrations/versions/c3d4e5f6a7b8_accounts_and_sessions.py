"""accounts, memberships, sessions, invitations, audit and quotas

Revision ID: c3d4e5f6a7b8
Revises: b7e1c4a9d2f3
Create Date: 2026-11-24
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b7e1c4a9d2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False, unique=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("totp_secret", sa.String(64)),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True)),
        sa.Column("failed_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "memberships",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="CASCADE"),
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("account_id", "country_id", name="uq_memberships_account_country"),
        sa.CheckConstraint(
            "role IN ('project_admin','country_admin','contributor','reviewer','viewer')",
            name="ck_memberships_role",
        ),
    )

    op.create_table(
        "sessions",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ip", sa.String(64)),
        sa.Column("user_agent", sa.Text()),
    )
    op.create_index("ix_sessions_account", "sessions", ["account_id"])
    op.create_index("ix_sessions_expires", "sessions", ["expires_at"])

    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="CASCADE"),
        ),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
    )

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "account_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("accounts.id", ondelete="SET NULL"),
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("object_type", sa.String(32)),
        sa.Column("object_id", sa.String(64)),
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="SET NULL"),
        ),
        sa.Column("detail", postgresql.JSONB()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_created", "audit_log", ["created_at"])
    op.create_index("ix_audit_country", "audit_log", ["country_id"])

    op.create_table(
        "quotas",
        sa.Column(
            "country_id",
            sa.Integer(),
            sa.ForeignKey("countries.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("cards_per_day", sa.Integer(), nullable=False, server_default="50"),
        sa.Column("gpu_seconds_per_day", sa.Integer(), nullable=False, server_default="1200"),
        sa.Column("max_concurrent", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("quotas")
    op.drop_index("ix_audit_country", table_name="audit_log")
    op.drop_index("ix_audit_created", table_name="audit_log")
    op.drop_table("audit_log")
    op.drop_table("invitations")
    op.drop_index("ix_sessions_expires", table_name="sessions")
    op.drop_index("ix_sessions_account", table_name="sessions")
    op.drop_table("sessions")
    op.drop_table("memberships")
    op.drop_table("accounts")
