"""Schema for the run ledger.

One database, one migration path. Images stay on disk; the database holds paths,
checksums and provenance. Every run is append-only: a correction is a new truth
version, a rerun is a new run.
"""

from __future__ import annotations

import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
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
