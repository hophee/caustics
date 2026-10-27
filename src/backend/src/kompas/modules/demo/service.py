"""Demo mode (D-088/D-130, only when ``KOMPAS_DEMO_MODE`` is on): both entries reuse a linked
synthetic patient and doctor, seeded through the real services (so the data passes the same
rules as real data). Every account made here is flagged ``demo`` and never shown on the admin page.
"""

import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.errors import InvalidInputError
from kompas.core.security import Role
from kompas.json_input import parse_diary_entry, parse_draft, parse_patient
from kompas.modules.appointments import service as appointments
from kompas.modules.appointments.models import AppointmentMode
from kompas.modules.assessments import service as assessments
from kompas.modules.assessments.models import AssignmentMode
from kompas.modules.audit import service as audit
from kompas.modules.cases import service as cases
from kompas.modules.cases.domain.profile import Sex
from kompas.modules.cases.models import VisitKind
from kompas.modules.diary import service as diary
from kompas.modules.identity import service as identity
from kompas.modules.identity.models import Doctor, Patient, User
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.therapy import service as therapy

SHARED_DOCTOR_EMAIL = "demo-doctor@demo.kompas"
TZ = "Europe/Moscow"
_FEMALE = ["Анна Смирнова", "Мария Кузнецова", "Елена Попова", "Ольга Соколова", "Наталья Лебедева"]
_MALE = ["Игорь Белов", "Дмитрий Морозов", "Сергей Волков", "Алексей Новиков", "Павел Фёдоров"]
# a calm week, then (for one doctor-demo patient) a worrying entry today
_DIARY = [
    (4, 6, 4, "poor", "Плохо спала, тревожно с утра."),
    (4, 6, 4, "poor", None),
    (5, 5, 5, "normal", "Чуть лучше, вышла погулять."),
    (5, 5, 5, "normal", None),
    (6, 4, 5, "normal", "Начала принимать таблетки утром и вечером, немного подташнивает."),
    (6, 4, 6, "good", None),
]
_URGENT = (2, 8, 2, "poor", "Ничего не помогает. Не хочу жить, устала быть обузой.")


@dataclass(frozen=True, slots=True)
class DemoAccount:
    user_id: uuid.UUID
    role: Role


def _secret_email(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(5)}@demo.kompas"


async def _shared_doctor(session: AsyncSession, now: datetime) -> Doctor:
    """The doctor every «как пациент» demo patient belongs to (created on first use)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext('kompas:demo-shared-doctor'))"))
    doctor = await session.scalar(
        select(Doctor).join(User, User.id == Doctor.user_id).where(User.email == SHARED_DOCTOR_EMAIL)
    )
    if doctor is not None:
        return doctor
    return await identity.create_doctor(
        session,
        email=SHARED_DOCTOR_EMAIL,
        password=secrets.token_urlsafe(24),  # demo entry issues a token; no password login
        full_name="Демо-врач Орлова Е. В.",
        specialty="психиатр",
        now=now,
        demo=True,
    )


async def _seed_patient(
    session: AsyncSession,
    knowledge: Knowledge,
    doctor: Doctor,
    *,
    invite_secret: str,
    now: datetime,
    urgent_today: bool,
    rng: random.Random,
) -> tuple[uuid.UUID, uuid.UUID]:
    """One synthetic patient with an account, profile, case (F41.1), prescription, a week of diary, a
    GAD-7 to fill and a planned visit. Returns (patient id, user id)."""
    doc = Actor(user_id=doctor.user_id, role=Role.DOCTOR, doctor_id=doctor.id, patient_id=None)
    sex = rng.choice([Sex.FEMALE, Sex.MALE])
    name = rng.choice(_FEMALE if sex is Sex.FEMALE else _MALE)
    birth = date(rng.randint(1970, 2000), rng.randint(1, 12), rng.randint(1, 28))
    start = now - timedelta(days=len(_DIARY))
    patient, _, _ = await identity.create_patient(
        session,
        doc,
        first_name=name.split()[0],
        last_name=name.split()[1],
        birth_date=birth,
        sex=sex,
        timezone=TZ,
        invite_secret=invite_secret,
        now=start,
    )
    user = await identity._new_user(
        session,
        _secret_email("demo-patient"),
        secrets.token_urlsafe(24),
        Role.PATIENT,
        start,
        full_name=name,
        demo=True,
    )
    patient.user_id, patient.invite_hmac, patient.invite_expires_at = user.id, None, None
    case = await cases.open_case(session, doc, patient.id, now=start)
    await cases.set_diagnosis(session, doc, case.id, icd_code="F41.1", is_main=True, provisional=False, now=start)
    profile = parse_patient(
        {
            "birth_date": birth.isoformat(),
            "sex": sex.value,
            "timezone": TZ,
            "pregnant": "no",
            "lactating": "no",
            "substance_use": "no",
            "suicide_risk": "high" if urgent_today else "low",
            "diagnoses": ["F41.1"],
            "allergies": [],
            "intolerant_excipients": [],
            "past_intolerance": [],
            "past_inefficacy": [],
            "weight_kg": rng.randint(55, 90),
        }
    )
    await cases.update_profile(session, doc, patient.id, profile, now=start)
    await _prescribe(session, knowledge, doc, patient.id, start)
    me = Actor(user_id=user.id, role=Role.PATIENT, doctor_id=None, patient_id=patient.id)
    days = [*_DIARY, _URGENT] if urgent_today else _DIARY
    for back, (mood, anxiety, energy, sleep, note) in zip(range(len(days) - 1, -1, -1), days, strict=True):
        moment = now - timedelta(days=back)
        local_day = moment.astimezone(ZoneInfo(TZ)).date()
        entry = parse_diary_entry(
            {
                "day": local_day.isoformat(),
                "mood": mood,
                "anxiety": anxiety,
                "energy": energy,
                "sleep": sleep,
                "self_harm_thoughts": False,
                "text": note,
            }
        )
        await diary.save_entry(session, me, knowledge, entry, submission_id=uuid.uuid4(), now=moment)
    today = now.astimezone(ZoneInfo(TZ)).date()
    if "GAD-7" in knowledge.scales:
        await assessments.assign(
            session,
            doc,
            case.id,
            knowledge,
            scale_code="GAD-7",
            mode=AssignmentMode.ONCE,
            every_days=None,
            starts_on=today,
            due_days=7,
            now=now,
        )
    visit_at = datetime.combine(today + timedelta(days=3), time(11, 30), ZoneInfo(TZ))
    await appointments.plan(
        session,
        doc,
        knowledge,
        patient.id,
        submission_id=uuid.uuid4(),
        starts_at=visit_at,
        duration_minutes=None,
        kind=VisitKind.REPEAT,
        mode=AppointmentMode.IN_PERSON,
        place="кабинет 214",
        link=None,
        purpose="оценка эффекта терапии",
        note_for_patient=None,
        now=now,
    )
    return patient.id, user.id


async def _prescribe(
    session: AsyncSession, knowledge: Knowledge, doc: Actor, patient_id: uuid.UUID, now: datetime
) -> None:
    """Авиандр (маритупирдин, КР F41.1) 20 mg morning and evening for 28 days (skipped without the
    drug base); red warnings, if any, get a demo justification, as a doctor would write one."""
    if knowledge.drugs is None or not knowledge.drugs.by_substance("маритупирдин"):
        return
    label = knowledge.drugs.by_substance("маритупирдин")[0].id
    start = now.astimezone(ZoneInfo(TZ)).date()
    draft = parse_draft(
        {
            "new_items": [
                {
                    "key": "a",
                    "label_id": label,
                    "origin": {"kind": "search"},
                    "regimen": {
                        "intakes": [
                            {"at": "08:00", "amount": 20, "unit": "мг"},
                            {"at": "20:00", "amount": 20, "unit": "мг"},
                        ],
                        "start": start.isoformat(),
                        "duration_days": 28,
                    },
                }
            ],
            "stopped": [],
        },
        knowledge.drugs,
    )
    current = await therapy.current_therapy(session, doc, patient_id)
    try:
        async with session.begin_nested():
            await therapy.confirm(
                session, doc, knowledge, patient_id, draft, {}, base_token=current.base_token, now=now
            )
    except InvalidInputError as exc:
        needed = {
            p.pointer: "Демо: решение врача, контроль через неделю"
            for p in exc.details
            if p.code == "justification_required" and p.pointer
        }
        if not needed:
            raise
        await therapy.confirm(
            session, doc, knowledge, patient_id, draft, needed, base_token=current.base_token, now=now
        )


async def demo_patient(
    session: AsyncSession, knowledge: Knowledge, *, invite_secret: str, now: datetime
) -> DemoAccount:
    """Reuse the same synthetic patient so switching roles preserves the entire test scenario."""
    doctor = await _demo_cohort(session, knowledge, invite_secret=invite_secret, now=now)
    patient = await session.scalar(
        select(Patient)
        .join(User, User.id == Patient.user_id)
        .where(Patient.treating_doctor_id == doctor.id, User.demo.is_(True), User.is_active.is_(True))
        .order_by(Patient.created_at.desc(), Patient.id.desc())
        .limit(1)
    )
    assert patient is not None and patient.user_id is not None
    patient_id, user_id = patient.id, patient.user_id
    await audit.record(
        session,
        None,
        "demo.session_started",
        entity_type="user",
        entity_id=user_id,
        patient_id=patient_id,
        details={"role": "patient"},
        now=now,
    )
    return DemoAccount(user_id, Role.PATIENT)


async def _demo_cohort(session: AsyncSession, knowledge: Knowledge, *, invite_secret: str, now: datetime) -> Doctor:
    """Shared synthetic-only cohort. The shared-doctor lock serializes first-time seeding.

    Existing patients (including earlier patient-demo sessions) are retained unchanged.
    Never return a normal account through the anonymous demo entry points.
    """
    doctor = await _shared_doctor(session, now)
    account = await session.get(User, doctor.user_id)
    if account is None or not account.demo or not account.is_active:
        raise InvalidInputError("Демонстрационная учётная запись недоступна")
    existing = await session.scalar(
        select(Patient.id)
        .join(User, User.id == Patient.user_id)
        .where(Patient.treating_doctor_id == doctor.id, User.demo.is_(True), User.is_active.is_(True))
        .limit(1)
    )
    if existing is None:
        rng = random.Random()
        for urgent in (True, False, False):
            await _seed_patient(
                session, knowledge, doctor, invite_secret=invite_secret, now=now, urgent_today=urgent, rng=rng
            )
    return doctor


async def demo_doctor(session: AsyncSession, knowledge: Knowledge, *, invite_secret: str, now: datetime) -> DemoAccount:
    """Open the treating doctor of the patient-demo account, retaining all test reports."""
    doctor = await _demo_cohort(session, knowledge, invite_secret=invite_secret, now=now)
    await audit.record(
        session,
        None,
        "demo.session_started",
        entity_type="user",
        entity_id=doctor.user_id,
        patient_id=None,
        details={"role": "doctor"},
        now=now,
    )
    return DemoAccount(doctor.user_id, Role.DOCTOR)
