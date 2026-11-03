"""location aliases — a reviewer's correction teaches the resolver

Revision ID: f3d9a1c4b7e8
Revises: a7c4e1f9b2d3
Create Date: 2026-11-03

One row per (country, normalised written spelling, card field). When a reviewer
confirms the gazetteer's chain or picks a candidate, the spellings the card
carried are stored with the canonical unit they resolved to. The next card that
writes the same spelling hits this table before fuzzy matching, so the same
misspelling never costs a second click. A later correction to the same spelling
updates the row (usage counted) — the evidence of what happened stays in
`runs.location`, which is append-only.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f3d9a1c4b7e8"
down_revision: Union[str, Sequence[str], None] = "a7c4e1f9b2d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "location_aliases",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("country", sa.String(64), nullable=False, server_default="mozambique"),
        # normalised spelling (casefold, accents stripped) — the lookup key
        sa.Column("written", sa.String(255), nullable=False),
        sa.Column("written_raw", sa.Text, nullable=True),
        # which card field the spelling came from: district | locality | village
        sa.Column("field", sa.String(16), nullable=False),
        sa.Column("level", sa.String(32), nullable=True),
        sa.Column("canonical", sa.String(255), nullable=False),
        sa.Column("pcode", sa.String(64), nullable=True),
        sa.Column("district", sa.String(255), nullable=True),
        sa.Column("province", sa.String(255), nullable=True),
        sa.Column("lat", sa.Float, nullable=True),
        sa.Column("lon", sa.Float, nullable=True),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source", sa.String(16), nullable=False, server_default="reviewer"),
        sa.Column("times_used", sa.Integer, nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
        sa.UniqueConstraint("country", "written", "field", name="uq_location_aliases_key"),
    )
    op.create_index("ix_location_aliases_written", "location_aliases", ["country", "written"])


def downgrade() -> None:
    op.drop_index("ix_location_aliases_written", table_name="location_aliases")
    op.drop_table("location_aliases")
