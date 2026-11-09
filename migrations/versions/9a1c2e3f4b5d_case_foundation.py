"""case foundation — case, capture, page, visit, closed section, confirmed value

Revision ID: 9a1c2e3f4b5d
Revises: f3d9a1c4b7e8
Create Date: 2026-11-09

The run ledger is per image. This is the durable case the next capture reads
back, shaped like a DHIS2 tracker so the later sync is a mapping, not a
migration. Additive: the run ledger and the web layer keep working unchanged
until the capture API is wired to these tables.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "9a1c2e3f4b5d"
down_revision: Union[str, Sequence[str], None] = "f3d9a1c4b7e8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cases",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("country_id", sa.Integer(), nullable=False),
        sa.Column("hanna_number", sa.String(length=16), nullable=True),
        sa.Column("government_id", sa.String(length=64), nullable=True),
        sa.Column("identity_status", sa.String(length=16), server_default="new", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("classification", sa.String(length=16), nullable=True),
        sa.Column("treatment_start_date", sa.Date(), nullable=True),
        sa.Column("outcome", sa.String(length=32), nullable=True),
        sa.Column("outcome_date", sa.Date(), nullable=True),
        sa.Column("outcome_note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "hanna_number IS NULL OR hanna_number ~ '^[0-9]{9}$'",
            name=op.f("ck_cases_hanna_number"),
        ),
        sa.CheckConstraint(
            "identity_status IN ('new','hanna_number','nil')",
            name=op.f("ck_cases_identity_status"),
        ),
        sa.CheckConstraint("status IN ('open','closed')", name=op.f("ck_cases_status")),
        sa.ForeignKeyConstraint(
            ["country_id"], ["countries.id"], name=op.f("fk_cases_country_id_countries")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cases")),
        sa.UniqueConstraint("country_id", "hanna_number", name="uq_cases_country_hanna"),
    )

    op.create_table(
        "captures",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=16), server_default="camera", nullable=False),
        sa.Column("attested_dummy", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("client", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="admitted", nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status IN ('admitted','rejected','processed')",
            name=op.f("ck_captures_status"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_captures_case_id_cases"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_captures")),
    )

    op.create_table(
        "capture_pages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("shot_no", sa.Integer(), nullable=False),
        sa.Column("image_path", sa.Text(), nullable=False),
        sa.Column("md5", sa.String(length=32), nullable=False),
        sa.Column("variant", sa.String(length=32), nullable=True),
        sa.Column("form_id", sa.Integer(), nullable=True),
        sa.Column("admission", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("match_inliers", sa.Integer(), nullable=True),
        sa.Column("match_margin", sa.Integer(), nullable=True),
        sa.Column("rotation", sa.Integer(), nullable=True),
        sa.Column("quality", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("image_state", sa.String(length=16), server_default="present", nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "admission IN ('pending','good','retake')",
            name=op.f("ck_capture_pages_admission"),
        ),
        sa.CheckConstraint(
            "image_state IN ('present','redacted','purged')",
            name=op.f("ck_capture_pages_image_state"),
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"], ["captures.id"],
            name=op.f("fk_capture_pages_capture_id_captures"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["form_id"], ["forms.id"], name=op.f("fk_capture_pages_form_id_forms")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_capture_pages")),
        sa.UniqueConstraint("capture_id", "page_no", name="uq_capture_pages_capture_page"),
    )

    op.create_table(
        "visits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=True),
        sa.Column("source_page_id", sa.Integer(), nullable=True),
        sa.Column("visit_seq", sa.Integer(), nullable=False),
        sa.Column("visit_label", sa.String(length=32), nullable=True),
        sa.Column("visit_date", sa.Date(), nullable=True),
        sa.Column("date_status", sa.String(length=16), server_default="recorded", nullable=False),
        sa.CheckConstraint(
            "date_status IN ('recorded','missing','inferred')",
            name=op.f("ck_visits_date_status"),
        ),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_visits_case_id_cases"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"], ["captures.id"],
            name=op.f("fk_visits_capture_id_captures"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_page_id"], ["capture_pages.id"],
            name=op.f("fk_visits_source_page_id_capture_pages"), ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_visits")),
        sa.UniqueConstraint("case_id", "visit_seq", name="uq_visits_case_seq"),
    )

    op.create_table(
        "section_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("visit_id", sa.Integer(), nullable=True),
        sa.Column("section_id", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), server_default="open", nullable=False),
        sa.Column("capture_id", sa.UUID(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_by", sa.String(length=64), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("state IN ('open','closed')", name=op.f("ck_section_states_state")),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_section_states_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["capture_id"], ["captures.id"],
            name=op.f("fk_section_states_capture_id_captures"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["visits.id"],
            name=op.f("fk_section_states_visit_id_visits"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_section_states")),
    )

    op.create_table(
        "case_values",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.UUID(), nullable=False),
        sa.Column("visit_id", sa.Integer(), nullable=True),
        sa.Column("concept_id", sa.String(length=64), nullable=False),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column("source_capture_id", sa.UUID(), nullable=True),
        sa.Column("source_page_id", sa.Integer(), nullable=True),
        sa.Column("confirmed_by", sa.String(length=64), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"], ["cases.id"], name=op.f("fk_case_values_case_id_cases"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["concept_id"], ["concepts.id"],
            name=op.f("fk_case_values_concept_id_concepts"),
        ),
        sa.ForeignKeyConstraint(
            ["source_capture_id"], ["captures.id"],
            name=op.f("fk_case_values_source_capture_id_captures"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["source_page_id"], ["capture_pages.id"],
            name=op.f("fk_case_values_source_page_id_capture_pages"), ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["visit_id"], ["visits.id"],
            name=op.f("fk_case_values_visit_id_visits"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_case_values")),
    )

    op.create_index("ix_cases_government", "cases", ["country_id", "government_id"])
    op.create_index("ix_visits_case_date", "visits", ["case_id", "visit_date"])
    op.create_index("ix_section_states_case", "section_states", ["case_id"])
    op.create_index("ix_case_values_case", "case_values", ["case_id"])

    # Postgres treats NULLs as distinct, so the case-level and visit-level
    # uniqueness of a section/value needs one partial index each.
    op.create_index(
        "uq_section_states_case_section",
        "section_states",
        ["case_id", "section_id"],
        unique=True,
        postgresql_where=sa.text("visit_id IS NULL"),
    )
    op.create_index(
        "uq_section_states_case_visit_section",
        "section_states",
        ["case_id", "visit_id", "section_id"],
        unique=True,
        postgresql_where=sa.text("visit_id IS NOT NULL"),
    )
    op.create_index(
        "uq_case_values_case_concept",
        "case_values",
        ["case_id", "concept_id"],
        unique=True,
        postgresql_where=sa.text("visit_id IS NULL"),
    )
    op.create_index(
        "uq_case_values_case_visit_concept",
        "case_values",
        ["case_id", "visit_id", "concept_id"],
        unique=True,
        postgresql_where=sa.text("visit_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_case_values_case_visit_concept", table_name="case_values")
    op.drop_index("uq_case_values_case_concept", table_name="case_values")
    op.drop_index("uq_section_states_case_visit_section", table_name="section_states")
    op.drop_index("uq_section_states_case_section", table_name="section_states")
    op.drop_index("ix_case_values_case", table_name="case_values")
    op.drop_index("ix_section_states_case", table_name="section_states")
    op.drop_index("ix_visits_case_date", table_name="visits")
    op.drop_index("ix_cases_government", table_name="cases")
    op.drop_table("case_values")
    op.drop_table("section_states")
    op.drop_table("visits")
    op.drop_table("capture_pages")
    op.drop_table("captures")
    op.drop_table("cases")
