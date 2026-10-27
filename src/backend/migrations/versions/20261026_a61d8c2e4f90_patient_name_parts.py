"""Store patient name parts separately."""

import sqlalchemy as sa
from alembic import op

revision = "a61d8c2e4f90"
down_revision = "9a4e6c1f0b2d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for name in ("last_name", "first_name", "middle_name"):
        op.add_column("patient", sa.Column(name, sa.String(60), nullable=True))
    # Legacy order is surname, given name, patronymic; VARCHAR rejects overflow without truncation.
    op.execute(r"""
        UPDATE patient SET
            last_name = split_part(trim(regexp_replace(full_name, '\s+', ' ', 'g')), ' ', 1),
            first_name = split_part(trim(regexp_replace(full_name, '\s+', ' ', 'g')), ' ', 2),
            middle_name = regexp_replace(trim(regexp_replace(full_name, '\s+', ' ', 'g')),
                                         '^\S+( \S+)? ?', '')
    """)
    for name in ("last_name", "first_name", "middle_name"):
        op.alter_column("patient", name, nullable=False)
    op.alter_column("patient", "middle_name", server_default="")
    op.drop_column("patient", "full_name")


def downgrade() -> None:
    op.add_column("patient", sa.Column("full_name", sa.String(), nullable=True))
    op.execute("UPDATE patient SET full_name = trim(last_name || ' ' || first_name || ' ' || middle_name)")
    op.alter_column("patient", "full_name", nullable=False)
    for name in ("middle_name", "first_name", "last_name"):
        op.drop_column("patient", name)
