"""API request/response shapes for identity."""

import uuid
import re
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from kompas.core.security import Role
from kompas.modules.cases.domain.profile import Sex


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: Role


class MeResponse(BaseModel):
    user_id: uuid.UUID
    role: Role
    full_name: str
    first_name: str | None = None
    doctor_id: uuid.UUID | None = None
    patient_id: uuid.UUID | None = None


class PersonName(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    last_name: str = Field(min_length=1, max_length=60)
    first_name: str = Field(min_length=1, max_length=60)
    middle_name: str = Field(default="", max_length=60)


class EmergencyContact(PersonName):
    role: str = Field(min_length=1, max_length=15)
    phone: str = Field(max_length=40)

    @field_validator("phone")
    @classmethod
    def normalize_phone(cls, value: str) -> str:
        if not re.fullmatch(r"\+?[0-9\s()-]+", value):
            raise ValueError("Укажите номер телефона")
        digits = re.sub(r"[^0-9]", "", value)
        if not digits.startswith(("7", "8")):
            digits = "7" + digits
        if len(digits) != 11:
            raise ValueError("Номер телефона должен содержать 11 цифр с кодом страны")
        return digits if digits.startswith("8") else "+" + digits


class PatientCreate(PersonName):
    """Doctor creates a patient card. ``sex`` and ``timezone`` are needed from day one (local dates,
    pregnancy applicability); every other safety fact starts as unknown (D-063)."""

    birth_date: date
    sex: Sex
    timezone: str = "Europe/Moscow"


class InviteIssued(BaseModel):
    patient_id: uuid.UUID
    invite_code: str  # shown once to the doctor
    expires_at: datetime


class PatientActivate(BaseModel):
    invite_code: str
    email: EmailStr
    password: str = Field(min_length=10)


class PatientListItem(BaseModel):
    last_name: str
    first_name: str
    middle_name: str
    patient_id: uuid.UUID
    full_name: str
    birth_date: date
    has_account: bool


class Page[T](BaseModel):
    items: list[T]
    total: int
    offset: int
    limit: int
