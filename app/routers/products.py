from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db

router = APIRouter(prefix="/products", tags=["catalog"])
PRODUCT_COLUMNS = """
    m.slug AS id, m.category_slug AS "categoryId", m.image_url AS "imageUrl",
    p.pro_nombre AS name, p.pro_descripcioncorta AS description,
    p.pro_precio_venta AS price, p.pro_saldo_final AS stock,
    c.cat_nombre AS "categoryLabel", b.mar_nombre AS brand
"""
PRODUCT_DETAIL_COLUMNS = PRODUCT_COLUMNS + ', m.product_id AS "productId"'
BASE_PRODUCT_SQL = """
    FROM apex_product_catalog m
    JOIN productos p ON p.id_producto = m.product_id
    JOIN categorias c ON c.id_categoria = p.id_categoria
    JOIN marcas b ON b.id_marca = p.id_marca
    WHERE p.estado_prod = 'ACT'
"""


def _serialize(row) -> dict:
    stock = max(int(row["stock"] or 0), 0)
    return {
        "id": row["id"],
        "categoryId": row["categoryId"],
        "categoryLabel": row["categoryLabel"].strip(),
        "name": row["name"].strip(),
        "description": (row["description"] or "").strip(),
        "priceCents": int(Decimal(row["price"]) * 100),
        "imageUrl": row["imageUrl"],
        "brand": row["brand"].strip(),
        "inStock": stock > 0,
    }


@router.get("")
def list_products(
    category: str | None = None,
    search: str | None = Query(default=None, max_length=100),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    conditions = []
    params: dict = {"limit": limit, "offset": offset}
    if category:
        conditions.append("AND m.category_slug = :category")
        params["category"] = category
    if search:
        conditions.append(
            "AND (p.pro_nombre ILIKE :search OR p.pro_descripcioncorta ILIKE :search "
            "OR b.mar_nombre ILIKE :search)"
        )
        params["search"] = f"%{search.strip()}%"
    condition_sql = " ".join(conditions)
    count = db.execute(
        text("SELECT count(*) " + BASE_PRODUCT_SQL + " " + condition_sql), params
    ).scalar_one()
    rows = db.execute(
        text(
            "SELECT " + PRODUCT_COLUMNS + " " + BASE_PRODUCT_SQL + " " + condition_sql
            + " ORDER BY p.pro_nombre, m.slug LIMIT :limit OFFSET :offset"
        ),
        params,
    ).mappings()
    return {
        "items": [_serialize(row) for row in rows],
        "total": count,
        "limit": limit,
        "offset": offset,
    }


@router.get("/categories")
def list_categories(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        text(
            "SELECT DISTINCT m.category_slug AS id, c.cat_nombre AS name "
            "FROM apex_product_catalog m JOIN productos p ON p.id_producto = m.product_id "
            "JOIN categorias c ON c.id_categoria = p.id_categoria "
            "WHERE p.estado_prod = 'ACT' ORDER BY c.cat_nombre"
        )
    ).mappings()
    return [{"id": row["id"], "name": row["name"].strip()} for row in rows]


@router.get("/cities")
def list_cities(db: Session = Depends(get_db)) -> list[dict]:
    rows = db.execute(
        text("SELECT id_ciudad AS id, ciu_descripcion AS name FROM ciudades ORDER BY ciu_descripcion")
    ).mappings()
    return [{"id": row["id"].strip(), "name": row["name"].strip()} for row in rows]


@router.get("/{slug}")
def get_product(slug: str, db: Session = Depends(get_db)) -> dict:
    row = db.execute(
        text("SELECT " + PRODUCT_DETAIL_COLUMNS + " " + BASE_PRODUCT_SQL + " AND m.slug = :slug"),
        {"slug": slug},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Product not found")
    photos = db.execute(
        text(
            "SELECT fot_url AS url FROM producto_fotografias "
            "WHERE id_producto = :product_id AND estado_fot = 'ACT' "
            "ORDER BY fot_orden, id_foto"
        ),
        {"product_id": row["productId"]},
    ).mappings()
    photo_urls = [photo["url"] for photo in photos if photo["url"] != row["imageUrl"]]
    urls = [row["imageUrl"], *photo_urls]
    product = _serialize(row)
    product["images"] = [
        {"url": url, "order": order}
        for order, url in enumerate(dict.fromkeys(urls), start=1)
    ]
    return product
