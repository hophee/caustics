"""results are doctor-only: drop the publication state (D-109)

Revision ID: 5d0b1c8e2f47
Revises: 3f9711d9bdf9
Create Date: 2026-10-25 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "5d0b1c8e2f47"
down_revision: str | None = "3f9711d9bdf9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint(op.f("fk_result_published_by_user_account"), "result", type_="foreignkey")
    for column in ("patient_texts", "publication", "status", "published_at", "published_by", "doctor_comment"):
        op.drop_column("result", column)


def downgrade() -> None:
    # Restored columns get neutral values: the texts and publication history are gone.
    op.add_column("result", sa.Column("doctor_comment", sa.String(), nullable=True))
    op.add_column("result", sa.Column("published_by", sa.Uuid(), nullable=True))
    op.add_column("result", sa.Column("published_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "result",
        sa.Column(
            "status",
            sa.Enum("published", "awaiting_doctor", "doctor_only", name="resultstatus", native_enum=False, length=40),
            nullable=False,
            server_default="doctor_only",
        ),
    )
    op.add_column(
        "result",
        sa.Column(
            "publication",
            sa.Enum("immediate", "after_doctor", "doctor_only", name="publication", native_enum=False, length=40),
            nullable=False,
            server_default="doctor_only",
        ),
    )
    op.add_column("result", sa.Column("patient_texts", sa.ARRAY(sa.Text()), nullable=False, server_default="{}"))
    op.create_foreign_key(
        op.f("fk_result_published_by_user_account"), "result", "user_account", ["published_by"], ["id"]
    )
