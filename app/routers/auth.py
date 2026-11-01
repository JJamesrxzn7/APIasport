import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import LoginRequest, RegisterRequest
from app.security import (
    Principal,
    current_principal,
    enforce_login_limit,
    hash_password,
    issue_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def _session_response(token: str, expires_at: datetime, principal: Principal) -> dict:
    return {
        "access_token": token,
        "token_type": "bearer",
        "expires_at": expires_at,
        "user": {
            "id": principal.id,
            "email": principal.email,
            "name": principal.name,
            "roles": sorted(principal.roles),
        },
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    enforce_login_limit(f"register:{request.client.host if request.client else 'unknown'}")
    parts = body.full_name.split()
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Enter first and last name")
    if len(body.identification) not in {10, 13}:
        raise HTTPException(status_code=422, detail="Identification must contain 10 or 13 digits")
    first_name = parts[0][:60]
    last_name = " ".join(parts[1:])[:60]
    user_id = secrets.token_urlsafe(16)[:16]
    customer_id = secrets.token_urlsafe(12)[:10]
    try:
        with db.begin():
            role = db.execute(
                text(
                    "SELECT id_rol, lower(trim(rol_nombre)) AS role_name FROM roles WHERE lower(trim(rol_nombre)) "
                    "IN ('cliente', 'customer', 'client') AND estado_rol = 'ACT' "
                    "ORDER BY id_rol LIMIT 1"
                )
            ).mappings().one_or_none()
            if role is None:
                raise HTTPException(
                    status_code=503,
                    detail="Customer role is not provisioned; contact the administrator",
                )
            city_exists = db.execute(
                text("SELECT 1 FROM ciudades WHERE id_ciudad = :city_id"),
                {"city_id": body.city_id},
            ).scalar_one_or_none()
            if city_exists is None:
                raise HTTPException(status_code=422, detail="Unknown city_id")
            db.execute(
                text(
                    "INSERT INTO usuarios "
                    "(id_usuario, usr_username, usr_passwordhash, usr_nombre, usr_apellido, usr_mail, "
                    "usr_telefono, estado_usr) VALUES "
                    "(:id, :username, :password_hash, :first_name, :last_name, :email, :phone, 'ACT')"
                ),
                {
                    "id": user_id,
                    "username": body.email,
                    "password_hash": hash_password(body.password),
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": body.email,
                    "phone": body.phone,
                },
            )
            db.execute(
                text(
                    "INSERT INTO usuariosxroles (id_usuario, id_rol, estado_uxr) "
                    "VALUES (:user_id, :role_id, 'ACT')"
                ),
                {"user_id": user_id, "role_id": role["id_rol"]},
            )
            db.execute(
                text(
                    "INSERT INTO clientes "
                    "(id_cliente, id_usuario, cli_nombre, cli_ruc_ced, cli_telefono, cli_mail, "
                    "id_ciudad, cli_direccion, estado_cli) "
                    "VALUES (:id, :user_id, :name, :identification, :phone, :email, :city, :address, 'ACT')"
                ),
                {
                    "id": customer_id,
                    "user_id": user_id,
                    "name": body.full_name[:40],
                    "identification": body.identification,
                    "phone": body.phone,
                    "email": body.email,
                    "city": body.city_id,
                    "address": body.address,
                },
            )
            token, expires_at = issue_token(db, user_id)
        principal = Principal(user_id, body.email, body.full_name, frozenset({role["role_name"]}))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email, username, or identification is already registered",
        ) from exc
    return _session_response(token, expires_at, principal)


@router.post("/login")
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db)) -> dict:
    email = body.email.lower()
    enforce_login_limit(f"login:{request.client.host if request.client else 'unknown'}")
    row = db.execute(
        text(
            "SELECT id_usuario, usr_passwordhash, usr_mail, usr_nombre, usr_apellido "
            "FROM usuarios WHERE lower(trim(usr_mail)) = :email AND estado_usr = 'ACT'"
        ),
        {"email": email},
    ).mappings().one_or_none()
    if row is None or not verify_password(row["usr_passwordhash"].strip(), body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    db.commit()
    with db.begin():
        token, expires_at = issue_token(db, row["id_usuario"].strip())
    roles = db.execute(
        text(
            "SELECT lower(trim(r.rol_nombre)) FROM usuariosxroles ur "
            "JOIN roles r ON r.id_rol = ur.id_rol "
            "WHERE ur.id_usuario = :user_id AND ur.estado_uxr = 'ACT' AND r.estado_rol = 'ACT'"
        ),
        {"user_id": row["id_usuario"]},
    ).scalars().all()
    principal = Principal(
        row["id_usuario"].strip(),
        row["usr_mail"].strip(),
        f'{row["usr_nombre"].strip()} {row["usr_apellido"].strip()}',
        frozenset(roles),
    )
    return _session_response(token, expires_at, principal)


@router.get("/me")
def me(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "id": principal.id,
        "email": principal.email,
        "name": principal.name,
        "roles": sorted(principal.roles),
    }


@router.delete("/session", status_code=status.HTTP_204_NO_CONTENT)
def revoke_session(
    request: Request,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> None:
    from fastapi.security.utils import get_authorization_scheme_param

    scheme, token = get_authorization_scheme_param(request.headers.get("authorization", ""))
    if scheme.lower() == "bearer" and token:
        import hashlib

        db.execute(
            text(
                "UPDATE apex_sessions SET revoked_at = :now "
                "WHERE token_hash = :token_hash AND user_id = :user_id AND revoked_at IS NULL"
            ),
            {
                "now": datetime.now(timezone.utc),
                "token_hash": hashlib.sha256(token.encode()).hexdigest(),
                "user_id": principal.id,
            },
        )
        db.commit()
