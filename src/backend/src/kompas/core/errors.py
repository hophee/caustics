"""Domain error hierarchy.

Domain code raises these; the API layer (later) maps them to HTTP responses, the CLI to messages.
Every error can carry structured ``details`` so clients can point at the exact field or item.
"""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Problem:
    """One machine-readable problem: ``code`` for programs, ``message`` (Russian) for people,
    ``pointer`` to what it concerns (e.g. ``"items[2].regimen"``, ``"answers.9"``, a warning id)."""

    code: str
    message: str
    pointer: str | None = None


class KompasError(Exception):
    """Base class for all expected, user-facing errors of the system."""

    code: str = "error"

    def __init__(self, message: str, details: tuple[Problem, ...] = ()) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class DefinitionError(KompasError):
    """A knowledge file (scale, rules, drug data) is malformed or internally inconsistent."""

    code = "invalid_definition"


class InvalidInputError(KompasError):
    """Input violates the domain contract (missing scale answers, bad regimen, ...). HTTP 422."""

    code = "invalid_input"


class InvalidStateError(KompasError):
    """The action is not allowed in the object's current state (publishing twice, ...). HTTP 409."""

    code = "invalid_state"


class NotFoundError(KompasError):
    """A referenced object does not exist (unknown label id, scale code, ...). HTTP 404."""

    code = "not_found"


class DrugDataUnavailableError(KompasError):
    """The drug catalog isn't loaded (no ОХЛП CSV configured). HTTP 503."""

    code = "drug_data_unavailable"


class UnauthenticatedError(KompasError):
    """No valid credentials (missing, invalid or expired token). HTTP 401."""

    code = "unauthenticated"


class PermissionDeniedError(KompasError):
    """Authenticated, but not allowed to see or change this object (another doctor's patient). HTTP 403."""

    code = "permission_denied"
