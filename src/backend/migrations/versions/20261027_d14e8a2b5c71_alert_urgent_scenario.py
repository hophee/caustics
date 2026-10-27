"""Urgent alert scenario: «опасность сейчас» / «уточнить состояние» (D-132).

Existing urgent alerts are backfilled from their source: questionnaire and structured diary answers →
``danger_now``; the text model and text-only diary alerts (dedup key ``…:urgent``) → ``check_soon``.
"""

import sqlalchemy as sa
from alembic import op

revision = "d14e8a2b5c71"
down_revision = "07ab8d410e52"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alert", sa.Column("scenario", sa.String(40), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE alert SET scenario = CASE
                WHEN source = 'diary_model' OR (source = 'diary' AND right(dedup_key, 7) = :text_only) THEN 'check_soon'
                ELSE 'danger_now'
            END
            WHERE level = 'urgent'
            """
        ).bindparams(text_only=":urgent")
    )


def downgrade() -> None:
    op.drop_column("alert", "scenario")
