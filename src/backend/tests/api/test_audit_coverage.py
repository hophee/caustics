"""Every write endpoint is either audited (action listed here, checked to appear in the code) or
explicitly exempt with a reason. A new POST/PUT route fails this test until it is classified."""

from pathlib import Path

from kompas.api.app import create_app

AUDITED = {
    "/api/v1/me/emergency-contacts": "patient.emergency_contact_added",
    "/api/v1/auth/activate": "patient.activated",
    "/api/v1/patients": "patient.created",
    "/api/v1/patients/{patient_id}/invite": "patient.invite_reissued",
    "/api/v1/patients/{patient_id}/transfer": "patient.transferred",
    "/api/v1/patients/{patient_id}/profile": "profile.updated",
    "/api/v1/patients/{patient_id}/profile/medications-reviewed": "profile.medications_reviewed",
    "/api/v1/patients/{patient_id}/cases": "case.opened",
    "/api/v1/cases/{case_id}/close": "case.closed",
    "/api/v1/cases/{case_id}/visits": "visit.added",
    "/api/v1/cases/{case_id}/visits/{visit_id}": "visit.updated",
    "/api/v1/patients/{patient_id}/therapy/current": "therapy.reconciled",
    "/api/v1/cases/{case_id}/diagnoses": "diagnosis.set",
    "/api/v1/cases/{case_id}/assignments": "assignment.created",
    "/api/v1/me/responses": "response.submitted",
    "/api/v1/cases/{case_id}/ratings": "response.submitted",
    "/api/v1/me/diary": "diary.saved",
    "/api/v1/diary/{entry_id}/notes": "diary.note_added",
    "/api/v1/patients/{patient_id}/basket/confirm": "prescription.confirmed",
    "/api/v1/me/drugs": "drug.reported",
    "/api/v1/alerts/{alert_id}/accept": "alert.accepted",
    "/api/v1/alerts/{alert_id}/handle": "alert.handled",
    "/api/v1/me/doses/marks": "dose.marked",
    "/api/v1/me/reminders": "reminders.updated",
    "/api/v1/patients/{patient_id}/referral-recommendations": "referral.recommended",
    "/api/v1/referral-recommendations/{recommendation_id}/decision": "referral.recommendation_decided",
    "/api/v1/patients/{patient_id}/referrals": "referral.issued",
    "/api/v1/referrals/{referral_id}/cancel": "referral.cancelled",
    "/api/v1/me/adverse-events": "adverse_event.reported",
    "/api/v1/me/reaction-signals": "adverse_event.signal",
    "/api/v1/me/reaction-signals/{event_id}": "adverse_event.details",
    "/api/v1/adverse-events/{event_id}/form": "adverse_event.form_saved",
    "/api/v1/me/adverse-events/{event_id}/ended": "adverse_event.ended",
    "/api/v1/patients/{patient_id}/adverse-events": "adverse_event.recorded",
    "/api/v1/adverse-events/{event_id}/review": "adverse_event.reviewed",
    "/api/v1/adverse-events/{event_id}/report-sent": "adverse_event.report_sent",
    "/api/v1/patients/{patient_id}/appointments": "appointment.planned",
    "/api/v1/appointments/{appointment_id}": "appointment.updated",
    "/api/v1/appointments/{appointment_id}/cancel": "appointment.cancelled",
    "/api/v1/appointments/{appointment_id}/complete": "appointment.completed",
    "/api/v1/appointments/{appointment_id}/summary": "appointment.summary_set",
    "/api/v1/appointment-requests/{request_id}/decision": "appointment.request_decided",
    "/api/v1/me/appointments/{appointment_id}/change-requests": "appointment.change_requested",
    "/api/v1/me/documents": "document.uploaded",
    "/api/v1/me/documents/{document_id}/remove": "document.removed",
    "/api/v1/patients/{patient_id}/documents": "document.issued",
    "/api/v1/patients/{patient_id}/documents/prescription": "document.issued",
    "/api/v1/documents/{document_id}/review": "document.reviewed",
    "/api/v1/patients/{patient_id}/demo-labs": "lab.demo_added",
    "/api/v1/patients/{patient_id}/demo-labs/{lab_id}": "lab.demo_removed",
    "/api/v1/auth/register": "user.registered",
    "/api/v1/auth/link": "patient.activated",
    "/api/v1/admin/users/{user_id}/doctor": "user.doctor_granted",
    "/api/v1/admin/users/{user_id}/revoke-doctor": "user.doctor_revoked",
    "/api/v1/demo/patient": "demo.session_started",
    "/api/v1/demo/doctor": "demo.session_started",
}
EXEMPT = {
    "/api/v1/auth/login": "issues a token, changes nothing",
    "/api/v1/patients/{patient_id}/basket/check": "stateless check, nothing stored",
    "/api/v1/alerts/{alert_id}/delivered": "browser receipt, timestamped on the alert itself",
    "/api/v1/me/notifications/{notification_id}/read": "read mark on one's own notification",
    "/api/v1/me/notifications/ticket": "short-lived stream ticket",
}
SRC = Path(__file__).resolve().parents[2] / "src" / "kompas"


def test_every_write_route_is_classified() -> None:
    app = create_app()
    paths = app.openapi()["paths"]
    writes = {path for path, ops in paths.items() if set(ops) & {"post", "put", "patch", "delete"}}
    assert writes - AUDITED.keys() - EXEMPT.keys() == set(), "classify new write routes"
    assert (AUDITED.keys() | EXEMPT.keys()) - writes == set(), "stale entries"


def test_audited_actions_exist_in_services() -> None:
    services = [p for p in SRC.rglob("*.py") if p.name in {"service.py", "accounts.py", "simple.py"}]
    code = "\n".join(p.read_text(encoding="utf-8") for p in services)
    for action in set(AUDITED.values()):
        assert f'"{action}"' in code, action
