import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from app.routers import auth
from app.schemas import AdminInvitationRequest, AdminRegistrationRequest
from app.security import Principal


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class InvitationSession:
    def __init__(self, invitation: dict[str, Any] | None = None) -> None:
        self.invitation = invitation
        self.statements: list[tuple[str, dict[str, Any]]] = []

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, parameters=None) -> Result:
        sql = str(statement)
        params = parameters or {}
        self.statements.append((sql, params))
        if "SELECT id_rol, lower(trim(rol_nombre))" in sql:
            return Result({"id_rol": "ROLE000001", "role_name": "administrador"})
        if "FROM usuariosxroles ur" in sql and "rolesxpermisos" in sql:
            return Result(1)
        if "SELECT 1 FROM usuarios" in sql:
            return Result(None)
        if "FROM apex_admin_invitations i JOIN roles r" in sql:
            return Result(self.invitation)
        return Result(None)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass


def test_admin_invitation_stores_only_token_digest(monkeypatch) -> None:
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    raw_token = "one-time-admin-invitation-token-1234567890"
    monkeypatch.setattr(auth.secrets, "token_urlsafe", lambda _size: raw_token)
    principal = Principal("admin-user-id", "admin@example.com", "Admin User", frozenset({"administrador"}))
    body = AdminInvitationRequest.model_validate({"email": "New.Admin@example.com"})
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    db = InvitationSession()

    response = auth.create_admin_invitation(body, request, principal, db)

    insert = next(
        params for sql, params in db.statements
        if "INSERT INTO apex_admin_invitations" in sql
    )
    assert response["invitation_token"] == raw_token
    assert response["email"] == "new.admin@example.com"
    assert insert["token_hash"] == hashlib.sha256(raw_token.encode()).hexdigest()
    assert raw_token not in insert.values()
    assert any("UPDATE apex_admin_invitations SET revoked_at" in sql for sql, _ in db.statements)
    assert any(params.get("action") == "CREATE_INVITE" for _, params in db.statements)


def test_admin_registration_consumes_invitation_and_assigns_invited_role(monkeypatch) -> None:
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    monkeypatch.setattr(auth, "hash_password", lambda _password: "argon2id-test-hash")
    issued_sessions: list[str] = []

    def issue_test_token(_db, user_id: str):
        issued_sessions.append(user_id)
        return "opaque-admin-session", datetime.now(timezone.utc)

    monkeypatch.setattr(auth, "issue_token", issue_test_token)
    invitation_token = "one-time-admin-invitation-token-1234567890"
    db = InvitationSession(
        {
            "id": 41,
            "email": "new.admin@example.com",
            "role_id": "ROLE000001",
            "role_name": "administrador",
        }
    )
    body = AdminRegistrationRequest.model_validate(
        {
            "invitation_token": invitation_token,
            "full_name": "Ada Lovelace",
            "password": "a-very-long-password",
        }
    )
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    response = auth.register_admin(body, request, db)

    user_insert = next(
        params for sql, params in db.statements if "INSERT INTO usuarios " in sql
    )
    role_insert = next(
        params for sql, params in db.statements if "INSERT INTO usuariosxroles" in sql
    )
    token_lookup = next(
        params for sql, params in db.statements if "FROM apex_admin_invitations i JOIN roles r" in sql
    )
    assert user_insert["email"] == "new.admin@example.com"
    assert user_insert["password_hash"] == "argon2id-test-hash"
    assert user_insert["first_name"] == "Ada"
    assert user_insert["last_name"] == "Lovelace"
    assert role_insert["role_id"] == "ROLE000001"
    assert role_insert["user_id"] == issued_sessions[0] == user_insert["id"]
    assert token_lookup["token_hash"] == hashlib.sha256(invitation_token.encode()).hexdigest()
    assert any("SET used_at = CURRENT_TIMESTAMP" in sql for sql, _ in db.statements)
    assert not any("INSERT INTO clientes" in sql for sql, _ in db.statements)
    assert response["access_token"] == "opaque-admin-session"
    assert response["user"]["roles"] == ["administrador"]
    assert "password" not in response


def test_admin_registration_rejects_invalid_invitation_before_creating_account(monkeypatch) -> None:
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    body = AdminRegistrationRequest.model_validate(
        {
            "invitation_token": "one-time-admin-invitation-token-1234567890",
            "full_name": "Ada Lovelace",
            "password": "a-very-long-password",
        }
    )
    db = InvitationSession(invitation=None)

    with pytest.raises(HTTPException) as error:
        auth.register_admin(body, SimpleNamespace(client=None), db)

    assert error.value.status_code == 400
    assert not any("INSERT INTO usuarios " in sql for sql, _ in db.statements)
