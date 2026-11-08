from contextlib import contextmanager
from collections.abc import Iterator
from decimal import Decimal
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.routers.checkout import create_order, get_my_order
from app.schemas import CheckoutRequest
from app.security import Principal

OWNER = Principal("USER00000000001", "owner@example.com", "Owner", frozenset({"cliente"}))
OTHER = Principal("USER00000000002", "other@example.com", "Other", frozenset({"cliente"}))


class CheckoutResult:
    def __init__(self, value: Any) -> None:
        self.value = value

    def mappings(self) -> "CheckoutResult":
        return self

    def scalars(self) -> "CheckoutResult":
        return self

    def all(self) -> Any:
        return self.value

    def one(self) -> Any:
        return self.value

    def scalar_one(self) -> Any:
        return self.value

    def scalar_one_or_none(self) -> Any:
        return self.value

    def __iter__(self):
        return iter(self.value)


class CheckoutSession:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.orders: dict[str, dict[str, Any]] = {}
        self.lines: dict[str, list[dict[str, Any]]] = {}
        self.profile = {"address": "Direccion fija", "phone": "0990000000", "city": "UIO"}

    @contextmanager
    def begin(self):
        yield self

    def execute(self, statement, parameters=None) -> CheckoutResult:
        sql = str(statement)
        params = parameters or {}
        self.statements.append(sql)
        if "SELECT id FROM apex_orders WHERE status = 'AWAITING_DEMO_PAYMENT'" in sql:
            return CheckoutResult([])
        if "pg_advisory_xact_lock" in sql:
            return CheckoutResult(None)
        if "idempotency_key = :key" in sql:
            return CheckoutResult(None)
        if "SELECT id_usuario FROM usuarios" in sql:
            return CheckoutResult(params["user_id"])
        if "SELECT id_cliente FROM clientes" in sql:
            return CheckoutResult("CLIENT0001")
        if "SELECT id_carrito FROM carrito" in sql:
            return CheckoutResult("CART000001")
        if "FROM detalle_carrito d JOIN apex_product_catalog" in sql:
            return CheckoutResult(
                [
                    {
                        "product_id": "SKU0000001",
                        "slug": "futbol-balon-pro-match",
                        "pro_nombre": "Balon Pro Match",
                        "pro_precio_venta": Decimal("10.00"),
                        "pro_saldo_final": 5,
                        "dtc_cantidad": 1,
                    }
                ]
            )
        if "SELECT count(*) FROM detalle_carrito" in sql:
            return CheckoutResult(1)
        if "INSERT INTO apex_orders" in sql:
            self.orders[params["id"]] = {
                "id": params["id"],
                "status": params["status"],
                "payment_status": "NOT_PROCESSED",
                "simulated_method": params["simulated_method"],
                "subtotal": params["subtotal"],
                "delivery_fee": params["fee"],
                "total": params["total"],
                "delivery_address": params["address"],
                "delivery_city": params["city"],
                "delivery_phone": params["phone"],
                "delivery_reference": params["reference"],
                "delivery_method": params["method"],
                "created_at": "2026-11-02 10:00:00",
                "expires_at": "2026-11-02 10:30:00",
            }
            return CheckoutResult(None)
        if "INSERT INTO apex_order_lines" in sql:
            self.lines.setdefault(params["order_id"], []).append(
                {
                    "product_slug": params["slug"],
                    "product_name": params["name"],
                    "quantity": params["quantity"],
                    "unit_price": params["price"],
                }
            )
            return CheckoutResult(None)
        if "SELECT id, status, payment_status, subtotal" in sql:
            return CheckoutResult(self.orders[params["id"]])
        if "SELECT product_slug, product_name, quantity, unit_price" in sql:
            return CheckoutResult(self.lines.get(params["order_id"], []))
        return CheckoutResult(None)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "before_cursor_execute", retval=True)
    def remove_postgres_row_locks(
        _connection, _cursor, statement, parameters, _context, _executemany
    ):
        return statement.replace(" FOR UPDATE SKIP LOCKED", ""), parameters

    with Session(engine) as session:
        session.execute(
            text(
                "CREATE TABLE apex_orders ("
                "id CHAR(15), user_id CHAR(16), status VARCHAR(32), payment_status VARCHAR(24), "
                "subtotal NUMERIC(10, 2), delivery_fee NUMERIC(10, 2), total NUMERIC(10, 2), simulated_method VARCHAR(16), "
                "delivery_address VARCHAR(120), delivery_city VARCHAR(60), delivery_phone VARCHAR(20), "
                "delivery_reference VARCHAR(120), delivery_method VARCHAR(16), "
                "created_at TIMESTAMP, expires_at TIMESTAMP)"
            )
        )
        session.execute(
            text(
                "CREATE TABLE apex_order_lines ("
                "order_id CHAR(15), product_slug VARCHAR(100), product_name VARCHAR(80), "
                "quantity INTEGER, unit_price NUMERIC(7, 2))"
            )
        )
        session.execute(
            text(
                "CREATE TABLE clientes ("
                "id_usuario CHAR(16), cli_direccion VARCHAR(60), cli_telefono VARCHAR(10), id_ciudad CHAR(3))"
            )
        )
        session.execute(
            text(
                "INSERT INTO clientes VALUES "
                "(:user_id, 'Direccion original del perfil', '0990000000', 'UIO')"
            ),
            {"user_id": OWNER.id},
        )
        for order_id, address, city, phone, reference in (
            ("ORDER0000000001", "Casa 1", "Quito", "0991111111", "Porton negro"),
            ("ORDER0000000002", "Trabajo 2", "Cuenca", "0982222222", "Recepcion"),
        ):
            session.execute(
                text(
                    "INSERT INTO apex_orders VALUES "
                    "(:id, :user_id, 'AWAITING_DEMO_PAYMENT', 'NOT_PROCESSED', "
                    "10.00, 0.00, 10.00, NULL, :address, :city, :phone, :reference, "
                    "'estandar', CURRENT_TIMESTAMP, '2099-01-01 00:00:00')"
                ),
                {
                    "id": order_id,
                    "user_id": OWNER.id,
                    "address": address,
                    "city": city,
                    "phone": phone,
                    "reference": reference,
                },
            )
        session.commit()
        yield session
    engine.dispose()


def test_orders_return_independent_delivery_snapshots_without_changing_profile(
    db: Session,
) -> None:
    first = get_my_order("ORDER0000000001", OWNER, db)
    db.commit()
    second = get_my_order("ORDER0000000002", OWNER, db)

    assert first["delivery"] == {
        "address": "Casa 1",
        "city": "Quito",
        "phone": "0991111111",
        "reference": "Porton negro",
        "method": "estandar",
    }
    assert second["delivery"] == {
        "address": "Trabajo 2",
        "city": "Cuenca",
        "phone": "0982222222",
        "reference": "Recepcion",
        "method": "estandar",
    }
    profile = db.execute(
        text("SELECT cli_direccion, cli_telefono, id_ciudad FROM clientes WHERE id_usuario = :id"),
        {"id": OWNER.id},
    ).one()
    assert tuple(profile) == ("Direccion original del perfil", "0990000000", "UIO")


def test_checkout_creates_separate_delivery_snapshots_without_updating_profile() -> None:
    db = CheckoutSession()
    first_request = CheckoutRequest(
        delivery_address="Casa 1",
        delivery_city="Quito",
        delivery_phone="0991111111",
        delivery_reference="Porton negro",
    )
    second_request = CheckoutRequest(
        delivery_address="Trabajo 2",
        delivery_city="Cuenca",
        delivery_phone="0982222222",
        delivery_reference="Recepcion",
    )

    first = create_order(first_request, "checkout-key-1", OWNER, db)
    second = create_order(second_request, "checkout-key-2", OWNER, db)

    assert first["delivery"]["address"] == "Casa 1"
    assert first["delivery"]["city"] == "Quito"
    assert first["delivery"]["phone"] == "0991111111"
    assert first["delivery"]["reference"] == "Porton negro"
    assert second["delivery"]["address"] == "Trabajo 2"
    assert second["delivery"]["city"] == "Cuenca"
    assert second["delivery"]["phone"] == "0982222222"
    assert second["delivery"]["reference"] == "Recepcion"
    assert db.profile == {"address": "Direccion fija", "phone": "0990000000", "city": "UIO"}
    assert not any("UPDATE clientes" in sql for sql in db.statements)


def test_customer_cannot_read_another_users_order(db: Session) -> None:
    with pytest.raises(HTTPException) as error:
        get_my_order("ORDER0000000001", OTHER, db)
    assert error.value.status_code == 404


@pytest.mark.parametrize(("method", "fee"), [("estandar", 200), ("express", 600)])
def test_checkout_calculates_delivery_fee_on_server(method: str, fee: int) -> None:
    db = CheckoutSession()
    request = CheckoutRequest(
        delivery_address="Casa 1",
        delivery_city="Quito",
        delivery_phone="0991111111",
        delivery_method=method,
    )

    order = create_order(request, f"checkout-{method}", OWNER, db)

    assert order["subtotalCents"] == 1000
    assert order["deliveryFeeCents"] == fee
    assert order["totalCents"] == 1000 + fee


@pytest.mark.parametrize(("method", "fee"), [("tarjeta", 200), ("transferencia", 200), ("retiro", 0)])
def test_simulated_checkout_records_method_and_correct_fee(method: str, fee: int) -> None:
    db = CheckoutSession()
    request = CheckoutRequest(
        delivery_address="Casa 1",
        delivery_city="Quito",
        delivery_phone="0991111111",
        simulated_method=method,
    )
    order = create_order(request, f"checkout-{method}", OWNER, db)
    assert order["status"] == "SIMULATED_COMPLETE"
    assert order["paymentStatus"] == "NOT_PROCESSED"
    assert order["simulatedMethod"] == method
    assert order["deliveryFeeCents"] == fee
    assert order["totalCents"] == 1000 + fee
