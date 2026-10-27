"""Background worker: delivers outbox messages, runs the diary text model, re-sends undelivered alerts.
Several workers may run at once (SKIP LOCKED + leases); alert topics can get their own pool.

    uv run kompas worker                               # all topics
    uv run kompas worker --topics alert.created        # dedicated alert pool
"""

import asyncio
import contextlib
import logging
import signal
import time
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.core import outbox
from kompas.core.clock import Clock, SystemClock
from kompas.core.config import Settings
from kompas.core.db import create_engine, create_session_factory, transaction
from kompas.core.outbox import Handler
from kompas.modules.adherence import service as adherence
from kompas.modules.appointments import service as appointments
from kompas.modules.diary import service as diary
from kompas.modules.diary.models import DiaryEntryRevision
from kompas.modules.identity.models import Patient
from kompas.modules.knowledge.bundle import Knowledge, load_knowledge
from kompas.modules.monitoring import service as monitoring
from kompas.modules.monitoring.domain.text_model import KeywordModel, ModelVerdict, TextTriageModel
from kompas.modules.notifications import service as notifications
from kompas.modules.notifications.models import Notification
from kompas.modules.therapy.models import Prescription

_log = logging.getLogger("kompas.worker")


def build_text_model(settings: Settings, knowledge: Knowledge) -> TextTriageModel:
    """The configured diary text model, loaded now (so a broken model is noticed at start-up).
    If the language model can't load, the worker falls back to keywords and says so in the log;
    verdicts then carry the keyword model's name, so the doctor's view shows which model ran."""
    if settings.text_model == "keywords":
        return KeywordModel(knowledge.triage_rules.keywords)
    from kompas.modules.monitoring.llm_model import LocalLLMModel  # imports torch lazily

    try:
        model = LocalLLMModel(
            settings.text_model_id,
            revision=settings.text_model_revision,
            prompt_style=settings.text_model_prompt,
            prompt_dir=settings.knowledge_dir / "text_model",
            threads=settings.text_model_threads,
        )
        model.load()
    except Exception:
        _log.exception("text model %s failed to load; falling back to keywords", settings.text_model_id)
        return KeywordModel(knowledge.triage_rules.keywords)
    _log.info("text model ready: %s", model.version)
    return model


def handlers(knowledge: Knowledge, clock: Clock, model: TextTriageModel | None = None) -> Mapping[str, Handler]:
    """Topic → handler: ``alert.created`` (monitoring.deliver), ``diary.model_triage`` (model in a
    thread, then diary.apply_model_verdict), ``prescription.confirmed`` (patient notification).
    ``model`` defaults to the keyword model until a local model beats it (task #6)."""
    text_model = model or KeywordModel(knowledge.triage_rules.keywords)

    async def alert_created(factory: async_sessionmaker[AsyncSession], payload: Mapping[str, Any]) -> None:
        async with transaction(factory) as session:
            await monitoring.deliver(session, payload, now=clock.now())

    async def classify(text: str) -> ModelVerdict | None:
        try:
            return await asyncio.to_thread(text_model.classify, text)  # outside any transaction
        except Exception:
            _log.exception("text model failed")
            return None

    async def diary_model(factory: async_sessionmaker[AsyncSession], payload: Mapping[str, Any]) -> None:
        revision_id = uuid.UUID(str(payload["revision_id"]))
        async with factory() as session:  # short read; released before the slow model runs
            revision = await session.get(DiaryEntryRevision, revision_id)
            text = revision.text if revision else None
        if not text:
            return
        verdict = await classify(text)
        async with transaction(factory) as session:
            await diary.apply_model_verdict(session, knowledge, revision_id, verdict, now=clock.now())

    async def prescription_confirmed(factory: async_sessionmaker[AsyncSession], payload: Mapping[str, Any]) -> None:
        async with transaction(factory) as session:
            prescription = await session.get(Prescription, uuid.UUID(str(payload["prescription_id"])))
            patient_id = uuid.UUID(str(payload["patient_id"]))
            user_id = await session.scalar(select(Patient.user_id).where(Patient.id == patient_id))
            if prescription is None or user_id is None:
                return
            already = await session.scalar(
                select(Notification.id).where(
                    Notification.user_id == user_id,
                    Notification.kind == "prescription",
                    Notification.payload["prescription_id"].astext == str(prescription.id),
                )
            )
            if already is None:  # idempotent under lease expiry
                await notifications.notify(
                    session,
                    user_id,
                    "prescription",
                    {"prescription_id": str(prescription.id), "version": prescription.version},
                    now=clock.now(),
                    patient_id=patient_id,
                )

    return {
        "alert.created": alert_created,
        "diary.model_triage": diary_model,
        "prescription.confirmed": prescription_confirmed,
    }


PERIODIC_SECONDS = {"reminders": 60.0, "skip_streaks": 1800.0, "appointment_reminders": 60.0}


async def run_periodic(
    factory: async_sessionmaker[AsyncSession], knowledge: Knowledge, clock: Clock, last_run: dict[str, float]
) -> None:
    """Time-driven jobs (dose and visit reminders every minute, skip-streak signals every 30 min). Each run takes a
    Postgres advisory lock, so across all workers only one runs a job at a time (the jobs are also
    idempotent in the database); ``last_run`` (monotonic seconds per job, kept by the caller) spaces
    them out within one process."""
    jobs = {
        "reminders": adherence.send_due_reminders,
        "skip_streaks": adherence.check_skip_streaks,
        "appointment_reminders": appointments.send_due_reminders,
    }
    engine = factory.kw["bind"]
    for name, job in jobs.items():
        now_mono = time.monotonic()
        if now_mono - last_run.get(name, -1e9) < PERIODIC_SECONDS[name]:
            continue
        last_run[name] = now_mono
        try:
            async with engine.connect() as conn:  # holds the session-level lock for the job's duration
                key = {"key": f"kompas:periodic:{name}"}
                if not await conn.scalar(text("SELECT pg_try_advisory_lock(hashtext(:key))"), key):
                    continue  # another worker is running it
                await conn.commit()
                try:
                    await job(factory, knowledge, now=clock.now())
                finally:
                    await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:key))"), key)
                    await conn.commit()
        except Exception:
            _log.exception("periodic job %s failed", name)


async def periodic_loop(
    factory: async_sessionmaker[AsyncSession], knowledge: Knowledge, clock: Clock, stopping: asyncio.Event
) -> None:
    """Runs the periodic jobs next to (not inside) the outbox loop, so a long reminder scan never
    delays alert delivery."""
    last_run: dict[str, float] = {}
    while not stopping.is_set():
        await run_periodic(factory, knowledge, clock, last_run)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stopping.wait(), timeout=10)


async def tick(
    factory: async_sessionmaker[AsyncSession],
    table: Mapping[str, Handler],
    settings: Settings,
    clock: Clock,
    *,
    topics: Sequence[str] | None = None,
    batch: int = 10,
) -> int:
    """One iteration: up to ``batch`` messages, each claimed (and leased) on its own right before it
    runs — so a lease never covers queued work and the highest-priority message (alerts) is picked
    next even mid-batch; then re-send undelivered alerts. Returns how many messages were processed."""
    processed = 0
    for _ in range(batch):
        async with transaction(factory) as session:
            messages = await outbox.claim(
                session, topics=topics, now=clock.now(), lease_seconds=settings.outbox_lease_seconds, limit=1
            )
            claimed = [(m.id, m.topic, dict(m.payload)) for m in messages]
        if not claimed:
            break
        message_id, topic, payload = claimed[0]
        processed += 1
        handler = table.get(topic)
        try:
            if handler is None:
                raise LookupError(f"no handler for topic {topic!r}")
            await handler(factory, payload)
        except Exception as exc:
            _log.exception("outbox %s (%s) failed", message_id, topic)
            async with transaction(factory) as session:
                await outbox.fail(
                    session,
                    message_id,
                    repr(exc),
                    now=clock.now(),
                    base_delay_seconds=settings.alert_retry_base_seconds,
                )
        else:
            async with transaction(factory) as session:
                await outbox.complete(session, message_id, now=clock.now())
    if topics is None or "alert.created" in topics:
        async with transaction(factory) as session:
            await monitoring.retry_undelivered(session, now=clock.now(), max_attempts=settings.alert_retry_max_attempts)
    return processed


async def run(
    settings: Settings,
    *,
    topics: Sequence[str] | None = None,
    once: bool = False,
    clock: Clock | None = None,
    knowledge: Knowledge | None = None,
) -> None:
    """Loop: claim → handle → complete/fail; tick ``monitoring.retry_undelivered``; sleep
    ``outbox_poll_seconds`` when idle. ``once`` = one iteration (tests). Stops cleanly on SIGTERM."""
    clock = clock or SystemClock()
    knowledge = knowledge or load_knowledge(settings.knowledge_dir, settings.ohlp_csv)
    engine = create_engine(settings.database_url)
    factory = create_session_factory(engine)
    table = handlers(knowledge, clock, build_text_model(settings, knowledge))
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, stopping.set)
    backoff = settings.outbox_poll_seconds
    periodic = None
    if not once and (topics is None or "periodic" in topics):
        periodic = asyncio.create_task(periodic_loop(factory, knowledge, clock, stopping), name="periodic-jobs")
    try:
        while not stopping.is_set():
            try:
                processed = await tick(factory, table, settings, clock, topics=topics)
                backoff = settings.outbox_poll_seconds
            except Exception:  # database restart, pool timeout …: keep the worker alive
                if once:
                    raise
                _log.exception("worker tick failed; retrying in %.1fs", backoff)
                processed = 0
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=backoff)
                backoff = min(backoff * 2, 60.0)
                continue
            if once:
                break
            if processed == 0:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(stopping.wait(), timeout=settings.outbox_poll_seconds)
    finally:
        stopping.set()
        if periodic is not None:
            await periodic
        await engine.dispose()
