"""drop doctor ↔ patient messages (D-120)

Revision ID: 9a4e6c1f0b2d
Revises: cc48d9959c9c
Create Date: 2026-10-26 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "9a4e6c1f0b2d"
down_revision: str | None = "cc48d9959c9c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Queued model runs would fail with no handler; «new message» notifications lead nowhere.
    # Alerts once raised from messages stay: they are clinical history and carry their own reason.
    op.execute("DELETE FROM outbox_message WHERE topic = 'message.model_triage'")
    op.execute("DELETE FROM notification WHERE kind = 'message'")
    op.drop_index("ix_message_patient_created", table_name="message")
    op.drop_table("message")


def downgrade() -> None:
    # The table comes back empty: the messages are gone.
    op.create_table(
        "message",
        sa.Column("patient_id", sa.Uuid(), nullable=False),
        sa.Column("author_user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "author_role", sa.Enum("doctor", "patient", name="role", native_enum=False, length=40), nullable=False
        ),
        sa.Column("text", sa.String(), nullable=False),
        sa.Column("submission_id", sa.Uuid(), nullable=False),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "triage_level",
            sa.Enum("stable", "attention", "urgent", name="triagelevel", native_enum=False, length=40),
            nullable=True,
        ),
        sa.Column("triage_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_pending", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["author_user_id"], ["user_account.id"], name=op.f("fk_message_author_user_id_user_account")
        ),
        sa.ForeignKeyConstraint(["patient_id"], ["patient.id"], name=op.f("fk_message_patient_id_patient")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_message")),
        sa.UniqueConstraint("author_user_id", "submission_id", name="uq_message_submission"),
    )
    op.create_index("ix_message_patient_created", "message", ["patient_id", "created_at"], unique=False)
