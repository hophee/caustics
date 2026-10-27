"""Actual medication reconciliation at the start of a visit."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c41b8d02a731"
down_revision = "9a4e6c1f0b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "current_therapy_review",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("case_id", sa.Uuid(), sa.ForeignKey("medical_case.id"), nullable=False),
        sa.Column("prescription_id", sa.Uuid(), sa.ForeignKey("prescription.id")),
        sa.Column("recorded_by", sa.Uuid(), sa.ForeignKey("user_account.id"), nullable=False),
        sa.Column("items", postgresql.JSONB(), nullable=False),
        sa.Column("reviewed_reported_ids", sa.ARRAY(sa.Text()), nullable=False),
    )
    op.create_index("ix_current_therapy_review_case_id", "current_therapy_review", ["case_id"])


def downgrade() -> None:
    op.drop_table("current_therapy_review")
