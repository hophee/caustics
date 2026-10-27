"""FastAPI application factory.

Startup loads the knowledge bundle once (scales, rules, guidelines, drug catalog) and creates the
database engine; both live on ``app.state``. Domain errors map to HTTP status codes in one place:
InvalidInput 422, InvalidState 409, NotFound 404, Unauthenticated 401, PermissionDenied 403,
DrugDataUnavailable 503, Definition 500 — always with the structured ``details`` (code, message,
pointer). Known ``IntegrityError`` constraint names (deterministic via the naming convention, e.g.
``uq_prescription_case_id``) map to 409 with a readable message. The ``NotificationHub`` is started
and stopped in the lifespan. Everything outside ``/api`` (and the docs) is the website: files from
``settings.web_dir``, else its ``index.html`` (client-side routes), same origin as the API (D-114).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from sqlalchemy.exc import IntegrityError

from kompas.core.clock import Clock, SystemClock
from kompas.core.config import Settings, load_settings
from kompas.core.db import create_engine, create_session_factory
from kompas.core.errors import (
    DefinitionError,
    DrugDataUnavailableError,
    InvalidInputError,
    InvalidStateError,
    KompasError,
    NotFoundError,
    PermissionDeniedError,
    UnauthenticatedError,
)
from kompas.core.jsonable import to_jsonable
from kompas.modules.adherence.router import router as adherence_router
from kompas.modules.adverse_events.router import router as adverse_events_router
from kompas.modules.appointments.router import router as appointments_router
from kompas.modules.assessments.router import router as assessments_router
from kompas.modules.cases.router import router as cases_router
from kompas.modules.demo.router import router as demo_router
from kompas.modules.diary.router import router as diary_router
from kompas.modules.documents.router import router as documents_router
from kompas.modules.identity.accounts_router import router as accounts_router
from kompas.modules.identity.router import router as identity_router
from kompas.modules.knowledge.bundle import Knowledge, load_knowledge
from kompas.modules.knowledge.router import router as knowledge_router
from kompas.modules.labs.router import router as labs_router
from kompas.modules.monitoring.router import router as monitoring_router
from kompas.modules.notifications.router import router as notifications_router
from kompas.modules.notifications.service import NotificationHub
from kompas.modules.overview.router import router as overview_router
from kompas.modules.referrals.router import router as referrals_router
from kompas.modules.therapy.router import router as therapy_router

STATUS = {
    InvalidInputError: 422,
    InvalidStateError: 409,
    NotFoundError: 404,
    UnauthenticatedError: 401,
    PermissionDeniedError: 403,
    DrugDataUnavailableError: 503,
    DefinitionError: 500,
}

CONSTRAINT_MESSAGES = {
    "uq_user_account_email_lower": "Этот email уже зарегистрирован",
    "uq_medical_case_one_active": "У пациента уже есть активный случай",
    "uq_prescription_case_version": "Назначение уже изменено другим подтверждением — обновите корзину",
    "uq_diagnosis_one_main": "Основной диагноз уже изменён — обновите страницу",
    "uq_response_submission": "Эта отправка уже обрабатывается",
    "uq_diary_entry_revision_submission": "Эта запись уже сохраняется",
    "uq_diary_entry_revision_revision": "Запись изменена одновременно — повторите сохранение",
    "uq_patient_reported_drug_submission": "Этот препарат уже добавлен",
    "uq_dose_mark_dose": "Этот приём уже отмечен — обновите список",
    "uq_dose_mark_submission": "Эта отметка уже сохраняется",
    "uq_adverse_event_submission": "Это сообщение о НЯ уже сохраняется",
    "uq_referral_submission": "Это направление уже оформляется",
    "uq_referral_recommendation_submission": "Эта рекомендация уже сохраняется",
    "uq_referral_recommendation_id": "По этой рекомендации направление уже оформлено",
}


def error_body(code: str, message: str, details: list[object]) -> dict[str, object]:
    return {"error": {"code": code, "message": message, "details": details}}


def create_app(
    settings: Settings | None = None, *, clock: Clock | None = None, knowledge: Knowledge | None = None
) -> FastAPI:
    """Build the app: lifespan (engine + knowledge), error handlers, routers under ``/api/v1``.

    Routers: auth, patients (cases, profile), assessments, diary, therapy (basket check/confirm,
    selection, prescriptions), alerts, notifications (SSE), knowledge (read-only lookups).
    ``clock`` and ``knowledge`` can be injected (tests, demos).
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        config = settings or load_settings()
        engine = create_engine(config.database_url)
        hub = NotificationHub(config.database_url)
        app.state.settings = config
        app.state.clock = clock or SystemClock()
        app.state.knowledge = knowledge or load_knowledge(config.knowledge_dir, config.ohlp_csv)
        app.state.engine = engine
        app.state.session_factory = create_session_factory(engine)
        app.state.hub = hub
        await hub.start()
        try:
            yield
        finally:
            await hub.stop()
            await engine.dispose()

    app = FastAPI(title="Клинический компас", version="0.1.0", lifespan=lifespan)

    @app.exception_handler(KompasError)
    async def domain_error(_request: Request, exc: KompasError) -> JSONResponse:
        status = next((code for kind, code in STATUS.items() if isinstance(exc, kind)), 400)
        return JSONResponse(error_body(exc.code, exc.message, to_jsonable(list(exc.details))), status_code=status)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        """Request-model errors in the same shape as domain errors (pointer = dotted field path)."""
        details: list[object] = [
            {
                "code": "invalid_input",
                "message": str(err.get("msg", "")),
                "pointer": ".".join(str(part) for part in err.get("loc", ())[1:]) or None,
            }
            for err in exc.errors()
        ]
        return JSONResponse(error_body("invalid_input", "Проверьте введённые данные", details), status_code=422)

    @app.exception_handler(IntegrityError)
    async def integrity_error(_request: Request, exc: IntegrityError) -> JSONResponse:
        constraint = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None) or ""
        message = CONSTRAINT_MESSAGES.get(constraint)
        if message is None:
            raise exc
        return JSONResponse(error_body("conflict", message, [{"constraint": constraint}]), status_code=409)

    for router in (
        identity_router,
        cases_router,
        assessments_router,
        diary_router,
        therapy_router,
        monitoring_router,
        notifications_router,
        knowledge_router,
        adherence_router,
        referrals_router,
        overview_router,
        adverse_events_router,
        appointments_router,
        documents_router,
        labs_router,
        accounts_router,
        demo_router,
    ):
        app.include_router(router, prefix="/api/v1")

    @app.get("/{path:path}", include_in_schema=False)
    async def website(path: str, request: Request) -> Response:
        """The built website; unknown non-API paths get index.html (client-side routing)."""
        root = request.app.state.settings.web_dir
        not_found = JSONResponse(error_body("not_found", "Не найдено", []), status_code=404)
        if root is None or path == "api" or path.startswith("api/") or not (root / "index.html").is_file():
            return not_found
        base = root.resolve()
        file = (base / path).resolve()
        if path and file.is_relative_to(base) and file.is_file():
            # hashed build assets never change; everything else is revalidated
            immutable = file.parent == base / "assets"
            cache = "public, max-age=31536000, immutable" if immutable else "no-cache"
            return FileResponse(file, headers={"Cache-Control": cache})
        if "." in path.rsplit("/", 1)[-1]:
            return not_found  # a missing file, not a page
        return FileResponse(base / "index.html", headers={"Cache-Control": "no-cache"})

    return app
