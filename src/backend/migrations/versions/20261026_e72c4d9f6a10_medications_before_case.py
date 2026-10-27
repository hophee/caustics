"""Allow medication reconciliation in a new patient's card before opening a case."""

import sqlalchemy as sa
from alembic import op

revision = "e72c4d9f6a10"
down_revision = "c41b8d02a731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("current_therapy_review", sa.Column("patient_id", sa.Uuid(), nullable=True))
    op.execute("UPDATE current_therapy_review r SET patient_id=c.patient_id FROM medical_case c WHERE r.case_id=c.id")
    op.alter_column("current_therapy_review", "patient_id", nullable=False)
    op.create_foreign_key("fk_therapy_review_patient", "current_therapy_review", "patient", ["patient_id"], ["id"])
    op.create_index("ix_current_therapy_review_patient_id", "current_therapy_review", ["patient_id"])
    op.alter_column("current_therapy_review", "case_id", nullable=True)


def downgrade() -> None:
    # Older schema cannot represent a pre-case review; refuse rather than discard data.
    op.alter_column("current_therapy_review", "case_id", nullable=False)
    op.drop_index("ix_current_therapy_review_patient_id", table_name="current_therapy_review")
    op.drop_constraint("fk_therapy_review_patient", "current_therapy_review", type_="foreignkey")
    op.drop_column("current_therapy_review", "patient_id")
