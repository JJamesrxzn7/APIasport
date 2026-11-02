from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.routers.favorites import add_favorite, list_favorites, remove_favorite
from app.security import Principal, current_principal

PRODUCT_ID = "SKU0000001"
PRODUCT_SLUG = "futbol-balon-pro-match"
USER_A = Principal("USER00000000001", "a@example.com", "User A", frozenset({"cliente"}))
USER_B = Principal("USER00000000002", "b@example.com", "User B", frozenset({"cliente"}))


@pytest.fixture
def db() -> Iterator[Session]:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with Session(engine) as session:
        session.execute(
            text(
                "CREATE TABLE favoritos ("
                "id_usuario CHAR(16) NOT NULL, id_producto CHAR(10) NOT NULL, "
                "PRIMARY KEY (id_usuario, id_producto))"
            )
        )
        session.execute(
            text(
                "CREATE TABLE apex_product_catalog ("
                "product_id CHAR(10) PRIMARY KEY, slug VARCHAR(100), "
                "category_slug VARCHAR(60), image_url VARCHAR(255))"
            )
        )
        session.execute(
            text(
                "CREATE TABLE productos ("
                "id_producto CHAR(10) PRIMARY KEY, pro_nombre VARCHAR(80), "
                "pro_descripcioncorta VARCHAR(255), pro_precio_venta TEXT, "
                "pro_saldo_final INTEGER, estado_prod CHAR(3), "
                "id_categoria CHAR(10), id_marca CHAR(10))"
            )
        )
        session.execute(
            text("CREATE TABLE categorias (id_categoria CHAR(10), cat_nombre VARCHAR(80))")
        )
        session.execute(text("CREATE TABLE marcas (id_marca CHAR(10), mar_nombre VARCHAR(80))"))
        session.execute(
            text(
                "INSERT INTO productos VALUES "
                "(:id, 'Balon Pro Match', 'Balon para partidos', 34.90, 8, 'ACT', 'CAT0000001', 'MAR0000001')"
            ),
            {"id": PRODUCT_ID},
        )
        session.execute(
            text(
                "INSERT INTO apex_product_catalog VALUES "
                "(:id, :slug, 'futbol', 'images/products/balon.svg')"
            ),
            {"id": PRODUCT_ID, "slug": PRODUCT_SLUG},
        )
        session.execute(
            text("INSERT INTO categorias VALUES ('CAT0000001', 'Futbol')")
        )
        session.execute(text("INSERT INTO marcas VALUES ('MAR0000001', 'Apex')"))
        session.commit()
        yield session
    engine.dispose()


def test_favorites_require_authentication() -> None:
    with pytest.raises(HTTPException) as error:
        current_principal(credentials=None, db=None)
    assert error.value.status_code == 401


def test_favorites_resolve_long_slug_and_add_idempotently(db: Session) -> None:
    assert len(PRODUCT_SLUG) > 10
    first = add_favorite(PRODUCT_SLUG, USER_A, db)
    db.commit()
    duplicate = add_favorite(PRODUCT_SLUG, USER_A, db)
    db.commit()

    expected_product = {
        "id": PRODUCT_SLUG,
        "categoryId": "futbol",
        "categoryLabel": "Futbol",
        "name": "Balon Pro Match",
        "description": "Balon para partidos",
        "priceCents": 3490,
        "imageUrl": "images/products/balon.svg",
        "brand": "Apex",
        "inStock": True,
    }
    assert first == {"items": [expected_product]}
    assert duplicate == first
    assert list_favorites(USER_A, db) == first
    saved_product_id = db.execute(
        text("SELECT id_producto FROM favoritos WHERE id_usuario = :user_id"),
        {"user_id": USER_A.id},
    ).scalar_one()
    assert saved_product_id == PRODUCT_ID
    assert saved_product_id != PRODUCT_SLUG


def test_favorite_removal_is_idempotent_and_user_scoped(db: Session) -> None:
    add_favorite(PRODUCT_SLUG, USER_A, db)
    db.commit()
    add_favorite(PRODUCT_SLUG, USER_B, db)
    db.commit()

    assert remove_favorite(PRODUCT_SLUG, USER_A, db) == {"items": []}
    db.commit()
    assert remove_favorite(PRODUCT_SLUG, USER_A, db) == {"items": []}
    db.commit()
    assert list_favorites(USER_B, db)["items"][0]["id"] == PRODUCT_SLUG


def test_favorites_reject_unavailable_products(db: Session) -> None:
    with pytest.raises(HTTPException) as error:
        add_favorite("unknown-product", USER_A, db)
    assert error.value.status_code == 404

    db.execute(
        text("UPDATE productos SET estado_prod = 'INA' WHERE id_producto = :id"),
        {"id": PRODUCT_ID},
    )
    db.commit()
    with pytest.raises(HTTPException) as error:
        add_favorite(PRODUCT_SLUG, USER_A, db)
    assert error.value.status_code == 404
