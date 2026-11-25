"""roles table, membership location and status, account phone

Revision ID: d4e5f6a7b8c9
Revises: c3d4e5f6a7b8
Create Date: 2026-11-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROLES = [
    ("project_admin", "Project administrator", 0),
    ("country_admin", "Country administrator", 10),
    ("contributor", "Contributor", 20),
    ("reviewer", "Reviewer", 30),
    ("tester", "Tester", 40),
    ("notifier", "Clinician / notifier", 50),
    ("viewer", "Viewer", 60),
]


def upgrade() -> None:
    roles = op.create_table(
        "roles",
        sa.Column("name", sa.String(32), primary_key=True),
        sa.Column("label", sa.String(64), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="100"),
    )
    op.bulk_insert(
        roles,
        [{"name": n, "label": l, "rank": r} for n, l, r in ROLES],
    )

    op.add_column("accounts", sa.Column("phone", sa.String(32)))

    op.add_column("memberships", sa.Column("province", sa.String(64)))
    op.add_column("memberships", sa.Column("district", sa.String(64)))
    op.add_column("memberships", sa.Column("facility", sa.String(64)))
    op.add_column("memberships", sa.Column("staff_number", sa.String(64)))
    op.add_column(
        "memberships",
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
    )
    op.drop_constraint("ck_memberships_role", "memberships", type_="check")
    op.create_check_constraint(
        "ck_memberships_status",
        "memberships",
        "status IN ('pending','active','suspended')",
    )
    op.create_foreign_key(
        "fk_memberships_role_roles", "memberships", "roles", ["role"], ["name"]
    )


def downgrade() -> None:
    op.drop_constraint("fk_memberships_role_roles", "memberships", type_="foreignkey")
    op.drop_constraint("ck_memberships_status", "memberships", type_="check")
    op.drop_column("memberships", "status")
    op.drop_column("memberships", "staff_number")
    op.drop_column("memberships", "facility")
    op.drop_column("memberships", "district")
    op.drop_column("memberships", "province")
    op.add_column(
        "memberships",
        sa.Column("role", sa.String(32), nullable=False, server_default="viewer"),
    )
    op.drop_column("accounts", "phone")
    op.drop_table("roles")
