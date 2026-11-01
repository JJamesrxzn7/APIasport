import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db

_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=settings.password_hash_memory_kib,
    parallelism=2,
)
_bearer = HTTPBearer(auto_error=False)
_rate_lock = Lock()
_auth_attempts: dict[str, list[float]] = {}


@dataclass(frozen=True)
class Principal:
    id: str
    email: str
    name: str
    roles: frozenset[str]


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(encoded: str, password: str) -> bool:
    try:
        return _hasher.verify(encoded, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def issue_token(db: Session, user_id: str) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(40)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.auth_session_hours)
    db.execute(
        text(
            "INSERT INTO apex_sessions (token_hash, user_id, expires_at) "
            "VALUES (:token_hash, :user_id, :expires_at)"
        ),
        {
            "token_hash": hashlib.sha256(token.encode()).hexdigest(),
            "user_id": user_id,
            "expires_at": expires,
        },
    )
    return token, expires


def enforce_login_limit(key: str) -> None:
    now = datetime.now().timestamp()
    with _rate_lock:
        recent = [item for item in _auth_attempts.get(key, []) if now - item < 60]
        if len(recent) >= 8:
            raise HTTPException(
                status_code=429,
                detail="Too many authentication attempts; try again shortly",
                headers={"Retry-After": "60"},
            )
        recent.append(now)
        _auth_attempts[key] = recent


def current_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Principal:
    unauthorized = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    token_hash = hashlib.sha256(credentials.credentials.encode()).hexdigest()
    row = db.execute(
        text(
            "SELECT u.id_usuario, u.usr_mail, u.usr_nombre, u.usr_apellido "
            "FROM apex_sessions s JOIN usuarios u ON u.id_usuario = s.user_id "
            "WHERE s.token_hash = :token_hash AND s.revoked_at IS NULL "
            "AND s.expires_at > CURRENT_TIMESTAMP AND u.estado_usr = 'ACT'"
        ),
        {"token_hash": token_hash},
    ).mappings().one_or_none()
    if row is None:
        raise unauthorized
    roles = db.execute(
        text(
            "SELECT lower(trim(r.rol_nombre)) FROM usuariosxroles ur "
            "JOIN roles r ON r.id_rol = ur.id_rol "
            "WHERE ur.id_usuario = :user_id AND ur.estado_uxr = 'ACT' "
            "AND r.estado_rol = 'ACT'"
        ),
        {"user_id": row["id_usuario"]},
    ).scalars().all()
    db.commit()
    return Principal(
        id=row["id_usuario"].strip(),
        email=row["usr_mail"].strip(),
        name=f'{row["usr_nombre"].strip()} {row["usr_apellido"].strip()}',
        roles=frozenset(roles),
    )


def require_admin(principal: Principal = Depends(current_principal)) -> Principal:
    if not principal.roles.intersection({"admin", "administrador", "superadministrador"}):
        raise HTTPException(status_code=403, detail="Administrator role required")
    return principal
