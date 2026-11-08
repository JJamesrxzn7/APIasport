from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.routers.cart import get_cart, set_cart_item
from app.schemas import CartItemRequest
from app.security import Principal


USER = Principal("USER00000000001", "a@example.com", "User A", frozenset({"cliente"}))
PRODUCT_ID = "SKU0000001"
PRODUCT_SLUG = "mma-espinilleras-mma"


class CartTestSession(Session):
    def execute(self, statement, params=None, **kwargs):
        # SQLite does not support PostgreSQL's row-locking syntax.
        statement = text(str(statement).replace(" FOR UPDATE OF p", "").replace(" FOR UPDATE", ""))
        return super().execute(statement, params, **kwargs)


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with CartTestSession(engine) as session:
        for statement in (
            "CREATE TABLE usuarios (id_usuario CHAR(16) PRIMARY KEY)",
            "CREATE TABLE carrito (id_carrito INTEGER PRIMARY KEY, id_usuario CHAR(16) NOT NULL, "
            "car_fechacreacion TIMESTAMP NOT NULL, car_fechaultimamodif TIMESTAMP NOT NULL)",
            "CREATE TABLE productos (id_producto CHAR(10) PRIMARY KEY, pro_nombre VARCHAR(80), "
            "pro_precio_venta NUMERIC, pro_saldo_final INTEGER, estado_prod CHAR(3))",
            "CREATE TABLE apex_product_catalog (product_id CHAR(10), slug VARCHAR(100), image_url VARCHAR(255))",
            "CREATE TABLE detalle_carrito (id_detalle INTEGER PRIMARY KEY, id_carrito INTEGER NOT NULL, "
            "id_producto CHAR(10) NOT NULL, dtc_cantidad INTEGER NOT NULL, dtc_preciounitario NUMERIC NOT NULL)",
        ):
            session.execute(text(statement))
        session.execute(text("INSERT INTO usuarios VALUES (:id)"), {"id": USER.id})
        session.execute(
            text("INSERT INTO productos VALUES (:id, 'Espinilleras MMA', 39, 10, 'ACT')"),
            {"id": PRODUCT_ID},
        )
        session.execute(
            text("INSERT INTO apex_product_catalog VALUES (:id, :slug, 'images/espinilleras.svg')"),
            {"id": PRODUCT_ID, "slug": PRODUCT_SLUG},
        )
        session.commit()
        yield session
    engine.dispose()


def test_cart_inserts_and_updates_price_using_database_column(db: Session) -> None:
    first = set_cart_item(CartItemRequest(product_id=PRODUCT_SLUG, quantity=1), USER, db)
    assert first["items"][0]["quantity"] == 1
    assert first["totalCents"] == 3900

    db.execute(text("UPDATE productos SET pro_precio_venta = 40 WHERE id_producto = :id"), {"id": PRODUCT_ID})
    updated = set_cart_item(CartItemRequest(product_id=PRODUCT_SLUG, quantity=2), USER, db)

    assert updated["items"][0]["quantity"] == 2
    assert updated["totalCents"] == 8000
    assert get_cart(USER, db) == updated
    assert db.execute(text("SELECT dtc_preciounitario FROM detalle_carrito")).scalar_one() == 40
    assert db.execute(text("SELECT count(*) FROM detalle_carrito")).scalar_one() == 1
