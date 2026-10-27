"""Password hashing (argon2) and access tokens (JWT, HS256), plus HMAC helpers for one-time codes."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from kompas.core.errors import UnauthenticatedError

_HASHER = PasswordHasher()
_DUMMY_HASH = _HASHER.hash("dummy-password-for-timing")
_ALGORITHM = "HS256"


class Role(StrEnum):
    """A person registers as PENDING (no clinical access); an admin makes them a DOCTOR, a doctor's
    invite code makes them a PATIENT (D-088)."""

    DOCTOR = "doctor"
    PATIENT = "patient"
    PENDING = "pending"
    ADMIN = "admin"


@dataclass(frozen=True, slots=True)
class TokenClaims:
    """What an access token says: who, with which role, until when."""

    user_id: uuid.UUID
    role: Role
    issued_at: datetime  # whole seconds (JWT iat)
    expires_at: datetime


def hash_password(password: str) -> str:
    """argon2id hash including salt and parameters."""
    return _HASHER.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check; False on mismatch or malformed hash (never raises for bad input)."""
    try:
        return _HASHER.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def burn_password_check(password: str) -> None:
    """Spend the same time as a real check (used for unknown emails, so timing doesn't leak them)."""
    verify_password(password, _DUMMY_HASH)


def issue_token(user_id: uuid.UUID, role: Role, *, secret: str, minutes: int, now: datetime) -> str:
    """Signed JWT with ``sub``, ``role``, ``iat``, ``exp``."""
    issued = int(now.timestamp())
    claims = {"sub": str(user_id), "role": role.value, "iat": issued, "exp": issued + minutes * 60}
    return jwt.encode(claims, secret, algorithm=_ALGORITHM)


def read_token(token: str, *, secret: str, now: datetime) -> TokenClaims:
    """Validate signature and expiry.

    Raises:
        UnauthenticatedError: invalid, expired or malformed token.
    """
    try:
        data = jwt.decode(
            token,
            secret,
            algorithms=[_ALGORITHM],
            # time checks use the injected clock below, never the system clock
            options={"require": ["sub", "role", "iat", "exp"], "verify_exp": False, "verify_iat": False},
        )
        claims = TokenClaims(
            user_id=uuid.UUID(data["sub"]),
            role=Role(data["role"]),
            issued_at=datetime.fromtimestamp(data["iat"], UTC),
            expires_at=datetime.fromtimestamp(data["exp"], UTC),
        )
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise UnauthenticatedError("Недействительный токен") from exc
    if claims.expires_at <= now:  # checked against the injected clock, not the system clock
        raise UnauthenticatedError("Срок действия токена истёк")
    if claims.issued_at > now + timedelta(seconds=60):
        raise UnauthenticatedError("Недействительный токен")
    return claims


def new_code(length: int = 12) -> str:
    """High-entropy one-time code, base32 without padding (easy to read aloud and type)."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def code_hmac(code: str, *, secret: str) -> str:
    """HMAC-SHA256 of a normalised code (indexed lookup without storing the code)."""
    normalised = code.strip().upper().replace("-", "").replace(" ", "")
    return hmac.new(secret.encode(), normalised.encode(), hashlib.sha256).hexdigest()


def floor_to_second(moment: datetime) -> datetime:
    """Drop microseconds (to compare with JWT ``iat``)."""
    return moment - timedelta(microseconds=moment.microsecond)
