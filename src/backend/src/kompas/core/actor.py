"""Who is acting: passed from the API (or worker) into services. Defined in core so services don't
depend on the web layer."""

import uuid
from dataclasses import dataclass

from kompas.core.security import Role


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated caller. ``doctor_id`` is set for a doctor, ``patient_id`` for a patient;
    neither for a pending (registered, not linked yet) user or an admin."""

    user_id: uuid.UUID
    role: Role
    doctor_id: uuid.UUID | None
    patient_id: uuid.UUID | None

    @property
    def is_doctor(self) -> bool:
        return self.role is Role.DOCTOR

    @property
    def is_patient(self) -> bool:
        return self.role is Role.PATIENT

    @property
    def is_admin(self) -> bool:
        return self.role is Role.ADMIN
