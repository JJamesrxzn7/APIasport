from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import CartItemRequest
from app.security import Principal, current_principal

router = APIRouter(prefix="/cart", tags=["cart"])


def _cart_id(db: Session, user_id: str, create: bool = False) -> int | None:
    if create:
        db.execute(
            text("SELECT id_usuario FROM usuarios WHERE id_usuario = :user_id FOR UPDATE"),
            {"user_id": user_id},
        ).scalar_one()
    cart_id = db.execute(
        text(
            "SELECT id_carrito FROM carrito WHERE id_usuario = :user_id "
            "ORDER BY car_fechaultimamodif DESC, id_carrito DESC LIMIT 1"
        ),
        {"user_id": user_id},
    ).scalar_one_or_none()
    if cart_id is None and create:
        cart_id = db.execute(
            text(
                "INSERT INTO carrito (id_usuario, car_fechacreacion, car_fechaultimamodif) "
                "VALUES (:user_id, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) RETURNING id_carrito"
            ),
            {"user_id": user_id},
        ).scalar_one()
    return cart_id


def _cart_data(db: Session, cart_id: int | None) -> dict:
    if cart_id is None:
        return {"items": [], "totalCents": 0}
    rows = db.execute(
        text(
            "SELECT m.slug AS id, p.pro_nombre AS name, p.pro_precio_venta AS price, "
            "d.dtc_cantidad AS quantity, m.image_url AS image "
            "FROM detalle_carrito d JOIN apex_product_catalog m ON m.product_id = d.id_producto "
            "JOIN productos p ON p.id_producto = d.id_producto WHERE d.id_carrito = :cart_id "
            "ORDER BY p.pro_nombre"
        ),
        {"cart_id": cart_id},
    ).mappings()
    items = [
        {
            "id": row["id"],
            "name": row["name"].strip(),
            "priceCents": int(Decimal(row["price"]) * 100),
            "quantity": row["quantity"],
            "imageUrl": row["image"],
        }
        for row in rows
    ]
    return {
        "items": items,
        "totalCents": sum(item["priceCents"] * item["quantity"] for item in items),
    }


@router.get("")
def get_cart(
    principal: Principal = Depends(current_principal), db: Session = Depends(get_db)
) -> dict:
    return _cart_data(db, _cart_id(db, principal.id))


@router.put("/items", status_code=status.HTTP_200_OK)
def set_cart_item(
    body: CartItemRequest,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    cart_id = _cart_id(db, principal.id, create=True)
    product = db.execute(
        text(
            "SELECT m.product_id, p.pro_precio_venta, p.pro_saldo_final "
            "FROM apex_product_catalog m JOIN productos p ON p.id_producto = m.product_id "
            "WHERE m.slug = :slug AND p.estado_prod = 'ACT' FOR UPDATE OF p"
        ),
        {"slug": body.product_id},
    ).mappings().one_or_none()
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if body.quantity > int(product["pro_saldo_final"] or 0):
        raise HTTPException(status_code=409, detail="Requested quantity is not available")
    detail_id = db.execute(
        text(
            "SELECT id_detalle FROM detalle_carrito "
            "WHERE id_carrito = :cart_id AND id_producto = :product_id LIMIT 1"
        ),
        {"cart_id": cart_id, "product_id": product["product_id"]},
    ).scalar_one_or_none()
    values = {
        "cart_id": cart_id,
        "product_id": product["product_id"],
        "quantity": body.quantity,
        "price": product["pro_precio_venta"],
    }
    if detail_id is None:
        db.execute(
            text(
                "INSERT INTO detalle_carrito "
                "(id_carrito, id_producto, dtc_cantidad, dtc_preciounitario) "
                "VALUES (:cart_id, :product_id, :quantity, :price)"
            ),
            values,
        )
    else:
        db.execute(
            text(
                "UPDATE detalle_carrito SET dtc_cantidad = :quantity, dtc_preciounitario = :price "
                "WHERE id_detalle = :detail_id"
            ),
            {**values, "detail_id": detail_id},
        )
    db.execute(
        text(
            "UPDATE carrito SET car_fechaultimamodif = CURRENT_TIMESTAMP "
            "WHERE id_carrito = :cart_id"
        ),
        {"cart_id": cart_id},
    )
    db.commit()
    return _cart_data(db, cart_id)


@router.delete("/items/{product_slug}", status_code=status.HTTP_204_NO_CONTENT)
def remove_cart_item(
    product_slug: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> None:
    cart_id = _cart_id(db, principal.id)
    if cart_id is None:
        return
    db.execute(
        text(
            "DELETE FROM detalle_carrito WHERE id_carrito = :cart_id AND id_producto = "
            "(SELECT product_id FROM apex_product_catalog WHERE slug = :slug)"
        ),
        {"cart_id": cart_id, "slug": product_slug},
    )
    db.execute(
        text("UPDATE carrito SET car_fechaultimamodif = CURRENT_TIMESTAMP WHERE id_carrito = :cart_id"),
        {"cart_id": cart_id},
    )
    db.commit()
