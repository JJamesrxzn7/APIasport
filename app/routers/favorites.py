from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.security import Principal, current_principal

router = APIRouter(prefix="/favorites", tags=["favorites"])
PRODUCT_COLUMNS = """
    m.slug AS id, m.category_slug AS "categoryId", m.image_url AS "imageUrl",
    p.pro_nombre AS name, p.pro_descripcioncorta AS description,
    p.pro_precio_venta AS price, p.pro_saldo_final AS stock,
    c.cat_nombre AS "categoryLabel", b.mar_nombre AS brand
"""


def _favorite_items(db: Session, user_id: str) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT "
            + PRODUCT_COLUMNS
            + " FROM favoritos f "
            "JOIN apex_product_catalog m ON m.product_id = f.id_producto "
            "JOIN productos p ON p.id_producto = m.product_id "
            "JOIN categorias c ON c.id_categoria = p.id_categoria "
            "JOIN marcas b ON b.id_marca = p.id_marca "
            "WHERE f.id_usuario = :user_id AND p.estado_prod = 'ACT' "
            "ORDER BY p.pro_nombre, m.slug"
        ),
        {"user_id": user_id},
    ).mappings()
    return [
        {
            "id": row["id"],
            "categoryId": row["categoryId"],
            "categoryLabel": row["categoryLabel"].strip(),
            "name": row["name"].strip(),
            "description": (row["description"] or "").strip(),
            "priceCents": int(Decimal(row["price"]) * 100),
            "imageUrl": row["imageUrl"],
            "brand": row["brand"].strip(),
            "inStock": max(int(row["stock"] or 0), 0) > 0,
        }
        for row in rows
    ]


@router.get("")
def list_favorites(
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    return {"items": _favorite_items(db, principal.id)}


@router.put("/{product_slug}")
def add_favorite(
    product_slug: str = Path(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    with db.begin():
        product_id = db.execute(
            text(
                "SELECT m.product_id FROM apex_product_catalog m "
                "JOIN productos p ON p.id_producto = m.product_id "
                "WHERE m.slug = :slug AND p.estado_prod = 'ACT'"
            ),
            {"slug": product_slug},
        ).scalar_one_or_none()
        if product_id is None:
            raise HTTPException(status_code=404, detail="Active product not found")
        db.execute(
            text(
                "INSERT INTO favoritos (id_usuario, id_producto) "
                "VALUES (:user_id, :product_id) "
                "ON CONFLICT (id_usuario, id_producto) DO NOTHING"
            ),
            {"user_id": principal.id, "product_id": product_id},
        )
    return {"items": _favorite_items(db, principal.id)}


@router.delete("/{product_slug}")
def remove_favorite(
    product_slug: str = Path(
        min_length=1,
        max_length=100,
        pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$",
    ),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    with db.begin():
        product_id = db.execute(
            text("SELECT product_id FROM apex_product_catalog WHERE slug = :slug"),
            {"slug": product_slug},
        ).scalar_one_or_none()
        if product_id is not None:
            db.execute(
                text(
                    "DELETE FROM favoritos "
                    "WHERE id_usuario = :user_id AND id_producto = :product_id"
                ),
                {"user_id": principal.id, "product_id": product_id},
            )
    return {"items": _favorite_items(db, principal.id)}
