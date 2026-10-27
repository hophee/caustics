"""Simple patient reaction signals and editable pharmacovigilance form."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f19a4c207e31"
down_revision = "e72c4d9f6a10"
branch_labels = None
depends_on = None


def upgrade():
    op.alter_column("adverse_event", "started_on", nullable=True)
    op.add_column("adverse_event", sa.Column("simple_report", sa.Boolean(), nullable=False, server_default="false"))
    op.add_column("adverse_event", sa.Column("onset_text", sa.Text(), nullable=True))
    op.add_column("adverse_event", sa.Column("details_complete", sa.Boolean(), nullable=False, server_default="true"))
    op.add_column("adverse_event", sa.Column("report_form", postgresql.JSONB(), nullable=False, server_default="{}"))


def downgrade():
    # Refuse a lossy downgrade rather than invent onset dates for incomplete signals.
    op.alter_column("adverse_event", "started_on", nullable=False)
    for name in ("report_form", "details_complete", "onset_text", "simple_report"):
        op.drop_column("adverse_event", name)
