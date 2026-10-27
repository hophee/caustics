"""New diary fields; preserve historical scales."""

import sqlalchemy as sa
from alembic import op

revision = "a91c2d3e4f50"
down_revision = "f83a7b2c9d10"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("diary_entry_revision", sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"))
    op.add_column("diary_entry_revision", sa.Column("irritability", sa.Integer(), nullable=True))
    op.add_column("diary_entry_revision", sa.Column("sleep_hours", sa.Float(), nullable=True))
    op.add_column("diary_entry_revision", sa.Column("unusual_experiences", sa.Boolean(), nullable=True))


def downgrade() -> None:
    for name in ("unusual_experiences", "sleep_hours", "irritability", "schema_version"):
        op.drop_column("diary_entry_revision", name)
