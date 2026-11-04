from types import SimpleNamespace
from typing import Any

from app.routers.auth import me


class Result:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> "Result":
        return self

    def one_or_none(self) -> Any:
        return self.value


class ProfileSession:
    def __init__(self, customer: dict[str, str | None] | None) -> None:
        self.customer = customer
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def execute(self, statement, parameters=None) -> Result:
        self.statements.append((str(statement), parameters or {}))
        return Result(self.customer)


def test_me_returns_authenticated_customer_profile_details() -> None:
    principal = SimpleNamespace(
        id="customer-1",
        email="ada@example.com",
        name="Ada Lovelace",
        roles=frozenset({"cliente"}),
    )
    db = ProfileSession(
        {"identification": "1234567890", "city": "Quito", "phone": None}
    )

    response = me(principal, db)

    assert response == {
        "id": "customer-1",
        "email": "ada@example.com",
        "name": "Ada Lovelace",
        "roles": ["cliente"],
        "identification": "1234567890",
        "city": "Quito",
        "phone": None,
    }
    assert db.statements[0][1] == {"user_id": "customer-1"}


def test_me_returns_empty_customer_details_for_non_customer_accounts() -> None:
    principal = SimpleNamespace(
        id="admin-1",
        email="admin@example.com",
        name="Admin Apex",
        roles=frozenset({"superadministrador"}),
    )
    db = ProfileSession(None)

    response = me(principal, db)

    assert response["identification"] is None
    assert response["city"] is None
    assert response["phone"] is None
