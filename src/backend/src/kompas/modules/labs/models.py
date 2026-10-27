"""Table: demo_lab_value — demo lab results entered by the doctor (A13: no real lab module).

Every row is demo data by definition; the API always says so. Removal is a soft delete (audited), so a
prescription's stored warnings can still be traced to the value that produced them.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin


class DemoLabValue(IdMixin, TimestampMixin, Base):
    __tablename__ = "demo_lab_value"
    __table_args__ = (
        UniqueConstraint("entered_by", "submission_id", name="uq_demo_lab_value_submission"),
        Index("ix_demo_lab_value_patient", "patient_id", "code", "measured_on"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    code: Mapped[str]  # rules/labs.yaml tests[].code
    value: Mapped[float]
    measured_on: Mapped[date]
    entered_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    submission_id: Mapped[uuid.UUID]
    removed_at: Mapped[datetime | None]
    removed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
