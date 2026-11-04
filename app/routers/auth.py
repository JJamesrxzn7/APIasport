import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.config import settings
from app.database import get_db
from app.schemas import (
    AdminBootstrapRequest,
    AdminInvitationRequest,
    AdminRegistrationRequest,
    LoginRequest,
    RegisterRequest,
)
from app.security import (
    Principal,
    current_principal,
    enforce_login_limit,
    hash_password,
    issue_token,
    authorize_permission,
    require_admin,
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


def _write_admin_audit(db: Session, user_id: str, action: str, client_ip: str | None) -> None:
    write_audit(db, user_id, "APEX_ADMIN_INVITATIONS", action, client_ip=client_ip)


@router.post("/admin/bootstrap", status_code=status.HTTP_201_CREATED)
def bootstrap_admin(
    body: AdminBootstrapRequest,
    request: Request,
    bootstrap_secret: str | None = Header(default=None, alias="X-Admin-Bootstrap-Secret"),
    db: Session = Depends(get_db),
) -> dict:
    configured_secret = settings.admin_bootstrap_secret
    if configured_secret is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")

    client_ip = request.client.host if request.client else "unknown"
    enforce_login_limit(f"admin-bootstrap:{client_ip}")
    expected_secret = configured_secret.get_secret_value()
    if bootstrap_secret is None or not hmac.compare_digest(bootstrap_secret, expected_secret):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid bootstrap secret")

    parts = body.full_name.split()
    if len(parts) < 2:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Enter first and last name")

    first_name = parts[0][:60]
    last_name = " ".join(parts[1:])[:60]
    user_id = secrets.token_urlsafe(16)[:16]
    reserved_role_id = "SUP0000001"
    try:
        with db.begin():
            db.execute(text("SELECT pg_advisory_xact_lock(hashtext('apex-admin-bootstrap'))"))
            existing_admin = db.execute(
                text(
                    "SELECT 1 FROM usuariosxroles ur JOIN roles r ON r.id_rol = ur.id_rol "
                    "WHERE ur.estado_uxr = 'ACT' AND r.estado_rol = 'ACT' "
                    "AND lower(trim(r.rol_nombre)) IN "
                    "('admin', 'administrador', 'superadministrador') LIMIT 1"
                )
            ).scalar_one_or_none()
            if existing_admin is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An active administrator already exists; use the invitation flow",
                )

            email_exists = db.execute(
                text(
                    "SELECT 1 FROM usuarios WHERE lower(trim(usr_mail)) = :email "
                    "OR lower(trim(usr_username)) = :email"
                ),
                {"email": body.email},
            ).scalar_one_or_none()
            if email_exists is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this email already exists",
                )

            role = db.execute(
                text(
                    "SELECT id_rol FROM roles "
                    "WHERE lower(trim(rol_nombre)) = 'superadministrador' "
                    "ORDER BY id_rol LIMIT 1 FOR UPDATE"
                )
            ).mappings().one_or_none()
            if role is None:
                reserved_id_exists = db.execute(
                    text("SELECT 1 FROM roles WHERE id_rol = :role_id"),
                    {"role_id": reserved_role_id},
                ).scalar_one_or_none()
                if reserved_id_exists is not None:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=f"Role ID {reserved_role_id} is already in use; review the roles table",
                    )
                db.execute(
                    text(
                        "INSERT INTO roles (id_rol, rol_nombre, rol_descripcion, estado_rol) "
                        "VALUES (:id, 'superadministrador', 'Superadministrador de Apex Sports', 'ACT')"
                    ),
                    {"id": reserved_role_id},
                )
                role_id = reserved_role_id
            else:
                role_id = role["id_rol"]
                db.execute(
                    text("UPDATE roles SET estado_rol = 'ACT' WHERE id_rol = :role_id"),
                    {"role_id": role_id},
                )

            db.execute(
                text(
                    "INSERT INTO usuarios "
                    "(id_usuario, usr_username, usr_passwordhash, usr_nombre, usr_apellido, usr_mail, "
                    "usr_telefono, estado_usr) VALUES "
                    "(:id, :username, :password_hash, :first_name, :last_name, :email, NULL, 'ACT')"
                ),
                {
                    "id": user_id,
                    "username": body.email,
                    "password_hash": hash_password(body.password),
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": body.email,
                },
            )
            db.execute(
                text(
                    "INSERT INTO usuariosxroles (id_usuario, id_rol, estado_uxr) "
                    "VALUES (:user_id, :role_id, 'ACT')"
                ),
                {"user_id": user_id, "role_id": role_id},
            )
            token, expires_at = issue_token(db, user_id)
            write_audit(
                db,
                user_id,
                "APEX_ADMIN_BOOTSTRAP",
                "CREATE_SUPERADMIN",
                new_value={"role_id": role_id},
                client_ip=client_ip,
            )
        principal = Principal(user_id, body.email, body.full_name, frozenset({"superadministrador"}))
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create the administrator account",
        ) from exc
    return _session_response(token, expires_at, principal)


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
                    "phone": None,
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
                    "phone": None,
                    "email": body.email,
                    "city": body.city_id,
                    "address": None,
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


@router.post("/admin/invitations", status_code=status.HTTP_201_CREATED)
def create_admin_invitation(
    body: AdminInvitationRequest,
    request: Request,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    authorize_permission(db, principal, "users", "write")
    enforce_login_limit(f"admin-invitation:{principal.id}")
    raw_token = secrets.token_urlsafe(40)
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    client_ip = request.client.host if request.client else None
    try:
        with db.begin():
            role = db.execute(
                text(
                    "SELECT id_rol, lower(trim(rol_nombre)) AS role_name FROM roles "
                    "WHERE lower(trim(rol_nombre)) IN ('admin', 'administrador', 'superadministrador') "
                    "AND estado_rol = 'ACT' "
                    "ORDER BY CASE lower(trim(rol_nombre)) "
                    "WHEN 'admin' THEN 0 WHEN 'administrador' THEN 1 ELSE 2 END LIMIT 1"
                )
            ).mappings().one_or_none()
            if role is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No active administrator role is provisioned",
                )
            email_exists = db.execute(
                text(
                    "SELECT 1 FROM usuarios WHERE lower(trim(usr_mail)) = :email "
                    "OR lower(trim(usr_username)) = :email"
                ),
                {"email": body.email},
            ).scalar_one_or_none()
            if email_exists is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="An account with this email already exists",
                )
            db.execute(
                text(
                    "UPDATE apex_admin_invitations SET revoked_at = CURRENT_TIMESTAMP "
                    "WHERE lower(email) = :email AND used_at IS NULL AND revoked_at IS NULL"
                ),
                {"email": body.email},
            )
            db.execute(
                text(
                    "INSERT INTO apex_admin_invitations "
                    "(email, token_hash, role_id, created_by, expires_at) "
                    "VALUES (:email, :token_hash, :role_id, :created_by, :expires_at)"
                ),
                {
                    "email": body.email,
                    "token_hash": hashlib.sha256(raw_token.encode()).hexdigest(),
                    "role_id": role["id_rol"],
                    "created_by": principal.id,
                    "expires_at": expires_at,
                },
            )
            _write_admin_audit(db, principal.id, "CREATE_INVITE", client_ip)
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Could not create an invitation for this email",
        ) from exc
    return {
        "email": body.email,
        "invitation_token": raw_token,
        "role": role["role_name"],
        "expires_at": expires_at,
    }


@router.post("/admin/register", status_code=status.HTTP_201_CREATED)
def register_admin(
    body: AdminRegistrationRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    enforce_login_limit(f"admin-registration:{request.client.host if request.client else 'unknown'}")
    parts = body.full_name.split()
    if len(parts) < 2:
        raise HTTPException(status_code=422, detail="Enter first and last name")
    first_name = parts[0][:60]
    last_name = " ".join(parts[1:])[:60]
    token_hash = hashlib.sha256(body.invitation_token.encode()).hexdigest()
    client_ip = request.client.host if request.client else None
    try:
        with db.begin():
            invitation = db.execute(
                text(
                    "SELECT i.id, i.email, i.role_id, lower(trim(r.rol_nombre)) AS role_name "
                    "FROM apex_admin_invitations i JOIN roles r ON r.id_rol = i.role_id "
                    "WHERE i.token_hash = :token_hash AND i.used_at IS NULL AND i.revoked_at IS NULL "
                    "AND i.expires_at > CURRENT_TIMESTAMP AND r.estado_rol = 'ACT' "
                    "AND lower(trim(r.rol_nombre)) IN ('admin', 'administrador', 'superadministrador') "
                    "FOR UPDATE OF i"
                ),
                {"token_hash": token_hash},
            ).mappings().one_or_none()
            if invitation is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Invitation is invalid, expired, revoked, or already used",
                )
            user_id = secrets.token_urlsafe(16)[:16]
            db.execute(
                text(
                    "INSERT INTO usuarios "
                    "(id_usuario, usr_username, usr_passwordhash, usr_nombre, usr_apellido, usr_mail, "
                    "usr_telefono, estado_usr) VALUES "
                    "(:id, :username, :password_hash, :first_name, :last_name, :email, NULL, 'ACT')"
                ),
                {
                    "id": user_id,
                    "username": invitation["email"],
                    "password_hash": hash_password(body.password),
                    "first_name": first_name,
                    "last_name": last_name,
                    "email": invitation["email"],
                },
            )
            db.execute(
                text(
                    "INSERT INTO usuariosxroles (id_usuario, id_rol, estado_uxr) "
                    "VALUES (:user_id, :role_id, 'ACT')"
                ),
                {"user_id": user_id, "role_id": invitation["role_id"]},
            )
            db.execute(
                text(
                    "UPDATE apex_admin_invitations SET used_at = CURRENT_TIMESTAMP "
                    "WHERE id = :id AND used_at IS NULL AND revoked_at IS NULL"
                ),
                {"id": invitation["id"]},
            )
            token, expires_at = issue_token(db, user_id)
            _write_admin_audit(db, user_id, "REGISTER_INVITE", client_ip)
        principal = Principal(
            user_id,
            invitation["email"].strip(),
            f"{first_name} {last_name}",
            frozenset({invitation["role_name"]}),
        )
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email, username, or account identifier is already registered",
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
def me(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    customer = db.execute(
        text(
            "SELECT trim(c.cli_ruc_ced) AS identification, "
            "trim(c.cli_telefono) AS phone, trim(ci.ciu_descripcion) AS city "
            "FROM clientes c LEFT JOIN ciudades ci ON ci.id_ciudad = c.id_ciudad "
            "WHERE c.id_usuario = :user_id AND c.estado_cli = 'ACT' "
            "ORDER BY c.id_cliente LIMIT 1"
        ),
        {"user_id": principal.id},
    ).mappings().one_or_none()
    return {
        "id": principal.id,
        "email": principal.email,
        "name": principal.name,
        "roles": sorted(principal.roles),
        "identification": customer["identification"] if customer else None,
        "city": customer["city"] if customer else None,
        "phone": customer["phone"] if customer else None,
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
