"""Schema for the run ledger.

One database, one migration path. Images stay on disk; the database holds paths,
checksums and provenance. Every run is append-only: a correction is a new truth
version, a rerun is a new run.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

metadata = MetaData(naming_convention=convention)

countries = Table(
    "countries",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("code", String(32), nullable=False, unique=True),
    Column("name", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

concepts = Table(
    "concepts",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("who_name", Text),
    Column("value_type", String(32), nullable=False),
    Column("options", JSONB),
    Column("observed", JSONB),
    Column("aliases", JSONB),
    Column("value_aliases", JSONB),
    Column("sensitivity", String(16), nullable=False),
    Column("tier", String(16), nullable=False),
    Column("card_marks", JSONB),
    Column("unit", String(16)),
    Column("min_value", Integer),
    Column("sanity_max", Integer),
    Column("note", Text),
)

forms = Table(
    "forms",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("country_id", ForeignKey("countries.id"), nullable=False),
    Column("form_key", String(64), nullable=False),
    Column("version", Integer, nullable=False),
    Column("status", String(16), nullable=False, server_default="draft"),
    Column("definition", JSONB, nullable=False),
    Column("template_path", Text, nullable=False),
    Column("template_md5", String(32)),
    Column("reference_year", Integer),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("form_key", "version", name="uq_forms_key_version"),
)

form_blocks = Table(
    "form_blocks",
    metadata,
    Column("form_id", ForeignKey("forms.id", ondelete="CASCADE"), primary_key=True),
    Column("block_id", String(64), primary_key=True),
    Column("block_type", String(32), nullable=False),
    Column("bbox", JSONB),
    Column("hint", Text),
    Column("ordinal", Integer, nullable=False),
)

form_pairs = Table(
    "form_pairs",
    metadata,
    Column("form_id", ForeignKey("forms.id", ondelete="CASCADE"), primary_key=True),
    Column("pair_id", String(64), primary_key=True),
    Column("block_id", String(64), nullable=False),
    Column("concept_id", ForeignKey("concepts.id"), nullable=False),
    Column("value_type", String(32), nullable=False),
    Column("bbox", JSONB),
    Column("options", JSONB),
    Column("sensitivity", String(16), nullable=False),
    Column("label_local", Text),
    Column("derived", Boolean, nullable=False, server_default="false"),
    Column("ordinal", Integer, nullable=False),
    ForeignKeyConstraint(
        ["form_id", "block_id"],
        ["form_blocks.form_id", "form_blocks.block_id"],
        ondelete="CASCADE",
    ),
)

profiles = Table(
    "profiles",
    metadata,
    Column("id", String(64), primary_key=True),
    Column("provider", String(32), nullable=False),
    Column("model", String(128), nullable=False),
    Column("settings", JSONB, nullable=False),
    Column("prompt_version", String(32), nullable=False),
    Column("crop_strategy", String(32), nullable=False),
    Column("notes", Text),
)

cards = Table(
    "cards",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("country_id", ForeignKey("countries.id"), nullable=False),
    Column("kind", String(16), nullable=False),
    Column("image_path", Text, nullable=False),
    Column("md5", String(32), nullable=False, unique=True),
    Column("width", Integer),
    Column("height", Integer),
    Column("attested_dummy", Boolean, nullable=False, server_default="false"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

runs = Table(
    "runs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("card_id", ForeignKey("cards.id"), nullable=False),
    Column("form_id", ForeignKey("forms.id"), nullable=False),
    Column("form_version", Integer, nullable=False),
    Column("profile_id", ForeignKey("profiles.id"), nullable=False),
    Column("status", String(16), nullable=False, server_default="running"),
    Column("purpose", String(32), nullable=False, server_default="slice"),
    Column("truth_version", Integer),
    Column("prompt_hash", String(64)),
    Column("alignment_ms", Integer),
    Column("started_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("validation", JSONB),
    Column("validation_status", String(16)),
    # Tiered location resolution: decision, level, point, score, alternatives.
    Column("location", JSONB),
    Column("latency_ms", Integer),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("cost_usd", Numeric(10, 6)),
    Column("error", Text),
    # Set when a reviewer presses Terminar on this run. Completion is a human
    # decision, not a coverage count: the screen hides empty visits and already
    # confirmed values, and those pairs can never be decided.
    Column("review_finished_at", DateTime(timezone=True)),
)

block_runs = Table(
    "block_runs",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("block_id", String(64), nullable=False),
    Column("block_type", String(32), nullable=False),
    Column("profile_id", String(64)),
    Column("prompt_hash", String(64)),
    Column("prompt", Text),
    Column("raw_response", Text),
    Column("status", String(16), nullable=False),
    Column("error", Text),
    Column("latency_ms", Integer),
    Column("input_tokens", Integer),
    Column("output_tokens", Integer),
    Column("crop_path", Text),
    UniqueConstraint("run_id", "block_id", name="uq_block_runs_run_block"),
)

observations = Table(
    "observations",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("run_id", ForeignKey("runs.id", ondelete="CASCADE"), nullable=False),
    Column("block_run_id", ForeignKey("block_runs.id", ondelete="CASCADE"), nullable=False),
    Column("block_id", String(64), nullable=False),
    Column("pair_id", String(64), nullable=False),
    Column("concept_id", String(64), nullable=False),
    Column("value", Text),
    Column("raw_value", Text),
    Column("confidence", Numeric(4, 3)),
    Column("expected_value", Text),
    Column("correct", Boolean),
    # What a human did with it: accepted, corrected (reviewed_value holds the fix), skipped.
    Column("review_mode", String(16)),
    Column("reviewed_value", Text),
    UniqueConstraint("run_id", "pair_id", name="uq_observations_run_pair"),
)

ground_truth = Table(
    "ground_truth",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("card_id", ForeignKey("cards.id"), nullable=False),
    Column("gt_key", String(64), nullable=False),
    Column("concept_id", String(64), nullable=False),
    Column("pair_id", String(64)),
    Column("value", Text, nullable=False),
    Column("status", String(16), nullable=False),
    Column("scoreable", Boolean, nullable=False, server_default="true"),
    Column("source", Text),
    Column("verified_by", Text),
    Column("verified_at", DateTime(timezone=True)),
    Column("version", Integer, nullable=False, server_default="1"),
    UniqueConstraint("card_id", "gt_key", "version", name="uq_ground_truth_card_key_version"),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("kind", String(32), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("status", String(16), nullable=False, server_default="queued"),
    Column("attempts", Integer, nullable=False, server_default="0"),
    Column("locked_by", String(64)),
    Column("locked_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("finished_at", DateTime(timezone=True)),
    Column("error", Text),
)

# --------------------------------------------------------------------------- case model
#
# The run ledger above records one reading of one image. The case model below is
# the durable record the next capture reads back: one case, many captures, many
# pages, visits, closed sections and the confirmed values. It follows the shape
# of a DHIS2 tracker (case = tracked entity, visit = event, concept = data
# element) so a later sync is a mapping, not a migration.

cases = Table(
    "cases",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("country_id", ForeignKey("countries.id"), nullable=False),
    # Nine digits, generated at first capture. Nullable only for imported legacy rows.
    Column("hanna_number", String(16)),
    # The government NIL, when the card carries one. Not unique: it is blank until
    # notification, and two patients cannot be keyed on a blank.
    Column("government_id", String(64)),
    # How this case was matched: a HANNA number read from the card, a NIL, or new.
    Column("identity_status", String(16), nullable=False, server_default="new"),
    # open = in treatment or unknown; closed = an outcome value and date are recorded.
    Column("status", String(16), nullable=False, server_default="open"),
    Column("classification", String(16)),  # PB | MB, from the card
    Column("treatment_start_date", Date),
    Column("outcome", String(32)),  # country-defined: completed, abandoned, died, ...
    Column("outcome_date", Date),
    Column("outcome_note", Text),
    # A re-scan whose number matches a case that already exists joins that case.
    # The provisional row stays only so an in-flight phone still resolves; every
    # reader follows this pointer.
    Column("merged_into", UUID(as_uuid=True), ForeignKey("cases.id", ondelete="SET NULL")),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint(
        "hanna_number IS NULL OR hanna_number ~ '^[0-9]{9}$'",
        name="hanna_number",
    ),
    CheckConstraint("identity_status IN ('new','hanna_number','nil')", name="identity_status"),
    CheckConstraint("status IN ('open','closed')", name="status"),
    UniqueConstraint("country_id", "hanna_number", name="uq_cases_country_hanna"),
)

captures = Table(
    "captures",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("case_id", ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
    Column("source", String(16), nullable=False, server_default="camera"),
    Column("attested_dummy", Boolean, nullable=False, server_default="false"),
    # Device and network details the field cannot standardise: user agent, model if
    # the browser exposes it, screen, camera capabilities, effective connection type.
    Column("client", JSONB),
    # admitted = every page passed the admission gate; rejected = retake requested.
    Column("status", String(16), nullable=False, server_default="admitted"),
    Column("captured_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    CheckConstraint("status IN ('admitted','rejected','processed')", name="status"),
)

capture_pages = Table(
    "capture_pages",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("capture_id", ForeignKey("captures.id", ondelete="CASCADE"), nullable=False),
    # page_no is the physical order (front=1, back=2, continuation=3...); shot_no is
    # the order the photos were taken, which the clinician may get wrong.
    Column("page_no", Integer, nullable=False),
    Column("shot_no", Integer, nullable=False),
    Column("image_path", Text, nullable=False),
    Column("md5", String(32), nullable=False),
    Column("variant", String(32)),  # front | back | page3, set by the page matcher
    Column("form_id", ForeignKey("forms.id"), nullable=True),
    # The extraction run for this page, once admission has passed.
    Column("run_id", ForeignKey("runs.id", ondelete="SET NULL"), nullable=True),
    # Binary gate: good or retake. pending until the matcher has run.
    Column("admission", String(16), nullable=False, server_default="pending"),
    Column("match_inliers", Integer),
    Column("match_margin", Integer),
    Column("rotation", Integer),
    Column("quality", JSONB),  # blur, brightness, glare, edge coverage, reasons
    # Retention: the row survives, the image may not.
    Column("image_state", String(16), nullable=False, server_default="present"),
    Column("purged_at", DateTime(timezone=True)),
    CheckConstraint("admission IN ('pending','good','retake')", name="admission"),
    CheckConstraint("image_state IN ('present','redacted','purged')", name="image_state"),
    UniqueConstraint("capture_id", "page_no", name="uq_capture_pages_capture_page"),
)

visits = Table(
    "visits",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("case_id", ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
    Column("capture_id", ForeignKey("captures.id", ondelete="SET NULL"), nullable=True),
    Column("source_page_id", ForeignKey("capture_pages.id", ondelete="SET NULL"), nullable=True),
    # The printed column position on the card (1..N). Stable across captures.
    Column("visit_seq", Integer, nullable=False),
    Column("visit_label", String(32)),  # the printed header, e.g. "3 meses"
    # Nullable: a column can be filled without a written date. That is data too.
    Column("visit_date", Date),
    Column("date_status", String(16), nullable=False, server_default="recorded"),
    CheckConstraint("date_status IN ('recorded','missing','inferred')", name="date_status"),
    UniqueConstraint("case_id", "visit_seq", name="uq_visits_case_seq"),
)

section_states = Table(
    "section_states",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("case_id", ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
    # NULL for a case-level section (registration, diagnosis, initial assessment).
    Column("visit_id", ForeignKey("visits.id", ondelete="CASCADE"), nullable=True),
    Column("section_id", String(64), nullable=False),
    Column("state", String(16), nullable=False, server_default="open"),
    Column("capture_id", ForeignKey("captures.id", ondelete="SET NULL"), nullable=True),
    Column("confirmed_by", String(64)),
    Column("confirmed_at", DateTime(timezone=True)),
    Column("reopened_by", String(64)),
    Column("reopened_at", DateTime(timezone=True)),
    Column("note", Text),
    CheckConstraint("state IN ('open','closed')", name="state"),
)

case_values = Table(
    "case_values",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("case_id", ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
    Column("visit_id", ForeignKey("visits.id", ondelete="CASCADE"), nullable=True),
    Column("concept_id", ForeignKey("concepts.id"), nullable=False),
    # NULL means the clinician confirmed the field is blank. A row is a fact.
    Column("value", Text),
    Column("source_capture_id", ForeignKey("captures.id", ondelete="SET NULL"), nullable=True),
    Column("source_page_id", ForeignKey("capture_pages.id", ondelete="SET NULL"), nullable=True),
    Column("confirmed_by", String(64)),
    Column("confirmed_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)

Index("ix_cases_government", cases.c.country_id, cases.c.government_id)
Index("ix_capture_pages_run", capture_pages.c.run_id)
Index("ix_visits_case_date", visits.c.case_id, visits.c.visit_date)
Index("ix_section_states_case", section_states.c.case_id)
Index("ix_case_values_case", case_values.c.case_id)

# A case-level section (visit_id NULL) and a visit-level section have separate
# uniqueness: Postgres treats NULLs as distinct, so the partial indexes do the work.
Index(
    "uq_section_states_case_section",
    section_states.c.case_id,
    section_states.c.section_id,
    unique=True,
    postgresql_where=section_states.c.visit_id.is_(None),
)
Index(
    "uq_section_states_case_visit_section",
    section_states.c.case_id,
    section_states.c.visit_id,
    section_states.c.section_id,
    unique=True,
    postgresql_where=section_states.c.visit_id.is_not(None),
)
Index(
    "uq_case_values_case_concept",
    case_values.c.case_id,
    case_values.c.concept_id,
    unique=True,
    postgresql_where=case_values.c.visit_id.is_(None),
)
Index(
    "uq_case_values_case_visit_concept",
    case_values.c.case_id,
    case_values.c.visit_id,
    case_values.c.concept_id,
    unique=True,
    postgresql_where=case_values.c.visit_id.is_not(None),
)


# --- accounts, roles and sessions (D-2026-11-17-02/03/04) -------------------
# One account may belong to one country; a project admin has no country and can
# act across them. Sessions are server-side rows keyed by the hash of a random
# cookie value, so a leaked database does not yield a usable cookie.

accounts = Table(
    "accounts",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("email", String(254), nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("display_name", String(128), nullable=False),
    Column("phone", String(32)),
    Column("is_active", Boolean, nullable=False, server_default="true"),
    Column("totp_secret", String(64)),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("last_login_at", DateTime(timezone=True)),
    Column("failed_attempts", Integer, nullable=False, server_default="0"),
    Column("locked_until", DateTime(timezone=True)),
)

roles = Table(
    "roles",
    metadata,
    Column("name", String(32), primary_key=True),
    Column("label", String(64), nullable=False),
    Column("rank", Integer, nullable=False, server_default="100"),
)

memberships = Table(
    "memberships",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "account_id",
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("country_id", ForeignKey("countries.id", ondelete="CASCADE")),
    Column("role", String(32), ForeignKey("roles.name"), nullable=False),
    Column("province", String(64)),
    Column("district", String(64)),
    Column("facility", String(64)),
    Column("staff_number", String(64)),
    Column("status", String(16), nullable=False, server_default="active"),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    UniqueConstraint("account_id", "country_id", name="uq_memberships_account_country"),
    CheckConstraint("status IN ('pending','active','suspended')", name="status"),
)

sessions = Table(
    "sessions",
    metadata,
    Column("id", String(64), primary_key=True),  # sha256 of the cookie value
    Column(
        "account_id",
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("last_seen_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("ip", String(64)),
    Column("user_agent", Text),
)
Index("ix_sessions_account", sessions.c.account_id)
Index("ix_sessions_expires", sessions.c.expires_at)

invitations = Table(
    "invitations",
    metadata,
    Column("id", UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
    Column("email", String(254), nullable=False),
    Column("country_id", ForeignKey("countries.id", ondelete="CASCADE")),
    Column("role", String(32), nullable=False),
    Column("token_hash", String(64), nullable=False, unique=True),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column(
        "created_by",
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
    Column("accepted_at", DateTime(timezone=True)),
    Column("revoked_at", DateTime(timezone=True)),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column(
        "account_id",
        UUID(as_uuid=True),
        ForeignKey("accounts.id", ondelete="SET NULL"),
    ),
    Column("action", String(64), nullable=False),
    Column("object_type", String(32)),
    Column("object_id", String(64)),
    Column("country_id", ForeignKey("countries.id", ondelete="SET NULL")),
    Column("detail", JSONB),
    Column("created_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
Index("ix_audit_created", audit_log.c.created_at)
Index("ix_audit_country", audit_log.c.country_id)

quotas = Table(
    "quotas",
    metadata,
    Column("country_id", ForeignKey("countries.id", ondelete="CASCADE"), primary_key=True),
    Column("cards_per_day", Integer, nullable=False, server_default="50"),
    Column("gpu_seconds_per_day", Integer, nullable=False, server_default="1200"),
    Column("max_concurrent", Integer, nullable=False, server_default="2"),
    Column("updated_at", DateTime(timezone=True), server_default=func.now(), nullable=False),
)
