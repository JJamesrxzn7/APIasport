from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException
from pydantic import SecretStr, ValidationError

from app.config import Settings
from app.routers import auth
from app.schemas import AdminBootstrapRequest


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class BootstrapSession:
    def __init__(
        self,
        *,
        existing_admin: bool = False,
        existing_email: bool = False,
        superadmin_role: dict[str, str] | None = None,
        reserved_role_id: bool = False,
    ) -> None:
        self.existing_admin = existing_admin
        self.existing_email = existing_email
        self.superadmin_role = superadmin_role
        self.reserved_role_id = reserved_role_id
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.transaction_count = 0

    @contextmanager
    def begin(self):
        self.transaction_count += 1
        yield self

    def execute(self, statement, parameters=None) -> Result:
        sql = str(statement)
        params = parameters or {}
        self.statements.append((sql, params))
        if "SELECT 1 FROM usuariosxroles ur JOIN roles r" in sql:
            return Result(1 if self.existing_admin else None)
        if "SELECT 1 FROM usuarios WHERE lower(trim(usr_mail))" in sql:
            return Result(1 if self.existing_email else None)
        if "WHERE lower(trim(rol_nombre)) = 'superadministrador'" in sql:
            return Result(self.superadmin_role)
        if "SELECT 1 FROM roles WHERE id_rol = :role_id" in sql:
            return Result(1 if self.reserved_role_id else None)
        return Result(None)

    def rollback(self) -> None:
        pass


def _request() -> SimpleNamespace:
    return SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))


def _body() -> AdminBootstrapRequest:
    return AdminBootstrapRequest.model_validate(
        {
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "a-very-long-password",
        }
    )


def test_bootstrap_creates_first_superadmin_once(monkeypatch) -> None:
    monkeypatch.setattr(auth, "settings", SimpleNamespace(admin_bootstrap_secret=SecretStr("s" * 48)))
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    monkeypatch.setattr(auth, "hash_password", lambda _password: "argon2id-test-hash")
    monkeypatch.setattr(auth, "issue_token", lambda _db, _user_id: ("opaque-token", datetime.now(timezone.utc)))
    audit_calls = []
    monkeypatch.setattr(auth, "write_audit", lambda *args, **kwargs: audit_calls.append((args, kwargs)))
    db = BootstrapSession()

    response = auth.bootstrap_admin(_body(), _request(), "s" * 48, db)

    user_insert = next(params for sql, params in db.statements if "INSERT INTO usuarios " in sql)
    role_insert = next(params for sql, params in db.statements if "INSERT INTO usuariosxroles" in sql)
    assert db.transaction_count == 1
    assert user_insert["email"] == "ada@example.com"
    assert user_insert["first_name"] == "Ada"
    assert user_insert["last_name"] == "Lovelace"
    assert user_insert["password_hash"] == "argon2id-test-hash"
    assert role_insert == {"user_id": user_insert["id"], "role_id": "SUP0000001"}
    assert response["user"]["roles"] == ["superadministrador"]
    assert response["access_token"] == "opaque-token"
    assert "password" not in response
    assert audit_calls


def test_bootstrap_is_disabled_without_deployment_secret(monkeypatch) -> None:
    monkeypatch.setattr(auth, "settings", SimpleNamespace(admin_bootstrap_secret=None))
    db = BootstrapSession()

    with pytest.raises(HTTPException) as error:
        auth.bootstrap_admin(_body(), _request(), "s" * 48, db)

    assert error.value.status_code == 404
    assert not db.statements


def test_bootstrap_rejects_wrong_secret(monkeypatch) -> None:
    monkeypatch.setattr(auth, "settings", SimpleNamespace(admin_bootstrap_secret=SecretStr("s" * 48)))
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    db = BootstrapSession()

    with pytest.raises(HTTPException) as error:
        auth.bootstrap_admin(_body(), _request(), "wrong", db)

    assert error.value.status_code == 403
    assert not db.statements


def test_bootstrap_secret_must_be_at_least_32_characters() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, admin_bootstrap_secret="s" * 31)

    settings = Settings(_env_file=None, admin_bootstrap_secret="s" * 32)
    assert settings.admin_bootstrap_secret == SecretStr("s" * 32)


@pytest.mark.parametrize(
    ("session_options", "expected_status"),
    [
        ({"existing_admin": True}, 409),
        ({"existing_email": True}, 409),
        ({"reserved_role_id": True}, 409),
    ],
)
def test_bootstrap_refuses_existing_admin_or_account(monkeypatch, session_options, expected_status) -> None:
    monkeypatch.setattr(auth, "settings", SimpleNamespace(admin_bootstrap_secret=SecretStr("s" * 48)))
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    db = BootstrapSession(**session_options)

    with pytest.raises(HTTPException) as error:
        auth.bootstrap_admin(_body(), _request(), "s" * 48, db)

    assert error.value.status_code == expected_status
    assert not any("INSERT INTO usuarios " in sql for sql, _ in db.statements)


def test_active_customer_registration_does_not_require_admin_invitation(monkeypatch) -> None:
    from app.schemas import RegisterRequest

    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    monkeypatch.setattr(auth, "hash_password", lambda _password: "argon2id-test-hash")
    monkeypatch.setattr(auth, "issue_token", lambda _db, _user_id: ("opaque-token", datetime.now(timezone.utc)))

    class CustomerSession(BootstrapSession):
        def execute(self, statement, parameters=None) -> Result:
            sql = str(statement)
            params = parameters or {}
            self.statements.append((sql, params))
            if "SELECT id_rol" in sql:
                return Result({"id_rol": "CLI0000001", "role_name": "cliente"})
            if "SELECT 1 FROM ciudades" in sql:
                return Result(1)
            return Result(None)

    body = RegisterRequest.model_validate(
        {
            "full_name": "Ada Lovelace",
            "email": "ada@example.com",
            "password": "a-very-long-password",
            "identification": "1234567890",
            "city_id": "UIO",
        }
    )
    db = CustomerSession()

    response = auth.register(body, _request(), db)

    assert response["user"]["roles"] == ["cliente"]
    assert any("INSERT INTO usuarios " in sql for sql, _ in db.statements)
