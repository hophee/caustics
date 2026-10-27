"""Add the patient's bounded emergency contact list."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b92e6a8c310f"
down_revision = "f83a7b2c9d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("patient", sa.Column("emergency_contacts", postgresql.JSONB(), nullable=False, server_default="[]"))
    op.create_check_constraint("emergency_contacts_limit", "patient", "jsonb_array_length(emergency_contacts) <= 5")


def downgrade() -> None:
    op.drop_constraint(op.f("ck_patient_emergency_contacts_limit"), "patient", type_="check")
    op.drop_column("patient", "emergency_contacts")
