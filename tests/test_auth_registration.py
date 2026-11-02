from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.routers import auth
from app.schemas import RegisterRequest


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value


class RegistrationSession:
    def __init__(self) -> None:
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
        if "SELECT id_rol" in sql:
            return Result({"id_rol": "ROLE000001", "role_name": "cliente"})
        if "SELECT 1 FROM ciudades" in sql:
            return Result(1)
        return Result(None)


def test_registration_persists_null_contact_fields_in_one_transaction(monkeypatch) -> None:
    monkeypatch.setattr(auth, "enforce_login_limit", lambda _key: None)
    monkeypatch.setattr(auth, "hash_password", lambda _password: "argon2id-test-hash")
    issued_sessions: list[str] = []

    def issue_test_token(_db, user_id: str):
        issued_sessions.append(user_id)
        return "opaque-test-token", datetime.now(timezone.utc)

    monkeypatch.setattr(
        auth,
        "issue_token",
        issue_test_token,
    )
    body = RegisterRequest.model_validate(
        {
            "full_name": "Ada Lovelace",
            "email": "Ada@example.com",
            "password": "a-very-long-password",
            "identification": "1234567890",
            "city_id": "UIO",
        }
    )
    db = RegistrationSession()

    response = auth.register(
        body,
        SimpleNamespace(client=SimpleNamespace(host="127.0.0.1")),
        db,
    )

    user_insert = next(sql for sql, _ in db.statements if "INSERT INTO usuarios " in sql)
    user_params = next(params for sql, params in db.statements if sql == user_insert)
    customer_insert = next(sql for sql, _ in db.statements if "INSERT INTO clientes " in sql)
    customer_params = next(params for sql, params in db.statements if sql == customer_insert)
    role_params = next(params for sql, params in db.statements if "INSERT INTO usuariosxroles" in sql)
    assert db.transaction_count == 1
    assert user_params["id"] == customer_params["user_id"] == role_params["user_id"]
    assert issued_sessions == [user_params["id"]]
    assert user_params["first_name"] == "Ada"
    assert user_params["last_name"] == "Lovelace"
    assert user_params["email"] == customer_params["email"] == "ada@example.com"
    assert customer_params["name"] == "Ada Lovelace"
    assert customer_params["identification"] == "1234567890"
    assert customer_params["city"] == "UIO"
    assert user_params["phone"] is None
    assert customer_params["phone"] is None
    assert customer_params["address"] is None
    assert user_params["password_hash"] == "argon2id-test-hash"
    assert "password" not in response
    assert response["access_token"] == "opaque-test-token"
