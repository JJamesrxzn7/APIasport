import secrets
import string
from decimal import Decimal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import CheckoutRequest
from app.security import Principal, current_principal

router = APIRouter(tags=["orders"])
ORDER_CHARS = string.ascii_uppercase + string.digits


def _order_payload(db: Session, order_id: str) -> dict:
    order = db.execute(
        text(
            "SELECT id, status, payment_status, subtotal, delivery_fee, total, "
            "delivery_address, delivery_city, delivery_phone, delivery_method, created_at, expires_at "
            "FROM apex_orders WHERE id = :id"
        ),
        {"id": order_id},
    ).mappings().one()
    lines = db.execute(
        text(
            "SELECT product_slug, product_name, quantity, unit_price "
            "FROM apex_order_lines WHERE order_id = :order_id ORDER BY product_name"
        ),
        {"order_id": order_id},
    ).mappings()
    return {
        "id": order["id"].strip(),
        "status": order["status"],
        "paymentStatus": order["payment_status"],
        "paymentMessage": "Demo checkout: no payment was processed.",
        "subtotalCents": int(Decimal(order["subtotal"]) * 100),
        "deliveryFeeCents": int(Decimal(order["delivery_fee"]) * 100),
        "totalCents": int(Decimal(order["total"]) * 100),
        "delivery": {
            "address": order["delivery_address"],
            "city": order["delivery_city"],
            "phone": order["delivery_phone"],
            "method": order["delivery_method"],
        },
        "createdAt": order["created_at"],
        "expiresAt": order["expires_at"],
        "items": [
            {
                "id": line["product_slug"],
                "name": line["product_name"],
                "quantity": line["quantity"],
                "priceCents": int(Decimal(line["unit_price"]) * 100),
            }
            for line in lines
        ],
    }


def release_expired_reservations(db: Session) -> int:
    expired_ids = db.execute(
        text(
            "SELECT id FROM apex_orders WHERE status = 'AWAITING_DEMO_PAYMENT' "
            "AND expires_at <= CURRENT_TIMESTAMP ORDER BY expires_at "
            "FOR UPDATE SKIP LOCKED"
        )
    ).scalars().all()
    for order_id in expired_ids:
        lines = db.execute(
            text(
                "SELECT product_id, quantity FROM apex_order_lines "
                "WHERE order_id = :id ORDER BY product_id"
            ),
            {"id": order_id},
        ).mappings().all()
        for line in lines:
            db.execute(
                text(
                    "UPDATE productos SET pro_saldo_final = pro_saldo_final + :quantity "
                    "WHERE id_producto = :product_id"
                ),
                line,
            )
            db.execute(
                text(
                    "INSERT INTO apex_stock_ledger "
                    "(order_id, product_id, quantity_delta, reason, created_at) "
                    "VALUES (:order_id, :product_id, :delta, 'checkout_expiry', CURRENT_TIMESTAMP)"
                ),
                {
                    "order_id": order_id,
                    "product_id": line["product_id"],
                    "delta": line["quantity"],
                },
            )
        db.execute(
            text("UPDATE apex_orders SET status = 'CANCELLED' WHERE id = :id"),
            {"id": order_id},
        )
    return len(expired_ids)


@router.post("/checkout", status_code=status.HTTP_201_CREATED)
def create_order(
    body: CheckoutRequest,
    idempotency_key: str = Header(alias="Idempotency-Key", min_length=8, max_length=128),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    new_order_id = "".join(secrets.choice(ORDER_CHARS) for _ in range(15))
    with db.begin():
        release_expired_reservations(db)
        db.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:lock_key, 0))"),
            {"lock_key": f"{principal.id}:{idempotency_key}"},
        )
        previous = db.execute(
            text(
                "SELECT id FROM apex_orders WHERE user_id = :user_id "
                "AND idempotency_key = :key FOR UPDATE"
            ),
            {"user_id": principal.id, "key": idempotency_key},
        ).scalar_one_or_none()
        if previous:
            order_id = previous
        else:
            db.execute(
                text("SELECT id_usuario FROM usuarios WHERE id_usuario = :user_id FOR UPDATE"),
                {"user_id": principal.id},
            ).scalar_one()
            client = db.execute(
                text("SELECT id_cliente FROM clientes WHERE id_usuario = :user_id AND estado_cli = 'ACT'"),
                {"user_id": principal.id},
            ).scalar_one_or_none()
            if client is None:
                raise HTTPException(status_code=409, detail="A customer profile is required to checkout")
            cart_id = db.execute(
                text(
                    "SELECT id_carrito FROM carrito WHERE id_usuario = :user_id "
                    "ORDER BY car_fechaultimamodif DESC, id_carrito DESC LIMIT 1 FOR UPDATE"
                ),
                {"user_id": principal.id},
            ).scalar_one_or_none()
            if cart_id is None:
                raise HTTPException(status_code=409, detail="Cart is empty")
            items = db.execute(
                text(
                    "SELECT m.product_id, m.slug, p.pro_nombre, p.pro_precio_venta, "
                    "p.pro_saldo_final, d.dtc_cantidad "
                    "FROM detalle_carrito d JOIN apex_product_catalog m ON m.product_id = d.id_producto "
                    "JOIN productos p ON p.id_producto = d.id_producto "
                    "WHERE d.id_carrito = :cart_id AND p.estado_prod = 'ACT' "
                    "ORDER BY m.product_id FOR UPDATE OF p"
                ),
                {"cart_id": cart_id},
            ).mappings().all()
            cart_item_count = db.execute(
                text("SELECT count(*) FROM detalle_carrito WHERE id_carrito = :cart_id"),
                {"cart_id": cart_id},
            ).scalar_one()
            if len(items) != cart_item_count:
                raise HTTPException(
                    status_code=409,
                    detail="One or more cart products are unavailable; update your cart",
                )
            if not items:
                raise HTTPException(status_code=409, detail="Cart is empty")
            for item in items:
                if int(item["pro_saldo_final"] or 0) < item["dtc_cantidad"]:
                    raise HTTPException(
                        status_code=409,
                        detail=f"Insufficient stock for {item['pro_nombre'].strip()}",
                    )

            subtotal = sum(
                (Decimal(item["pro_precio_venta"]) * item["dtc_cantidad"] for item in items),
                Decimal("0.00"),
            ).quantize(Decimal("0.01"))
            # Tax and delivery pricing are not defined by the supplied business schema.
            delivery_fee = Decimal("0.00")
            total = subtotal + delivery_fee
            db.execute(
                text(
                    "INSERT INTO apex_orders "
                    "(id, user_id, client_id, idempotency_key, status, payment_status, payment_method, "
                    "subtotal, delivery_fee, total, delivery_address, delivery_city, delivery_phone, "
                    "delivery_reference, delivery_method, created_at, expires_at) "
                    "VALUES (:id, :user, :client, :key, 'AWAITING_DEMO_PAYMENT', 'NOT_PROCESSED', "
                    "'demo', :subtotal, :fee, :total, :address, :city, :phone, :reference, "
                    ":method, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '30 minutes')"
                ),
                {
                    "id": new_order_id,
                    "user": principal.id,
                    "client": client,
                    "key": idempotency_key,
                    "subtotal": subtotal,
                    "fee": delivery_fee,
                    "total": total,
                    "address": body.delivery_address,
                    "city": body.delivery_city,
                    "phone": body.delivery_phone,
                    "reference": body.delivery_reference,
                    "method": body.delivery_method,
                },
            )
            for item in items:
                quantity = item["dtc_cantidad"]
                db.execute(
                    text(
                        "INSERT INTO apex_order_lines "
                        "(order_id, product_id, product_slug, product_name, quantity, unit_price) "
                        "VALUES (:order_id, :product_id, :slug, :name, :quantity, :price)"
                    ),
                    {
                        "order_id": new_order_id,
                        "product_id": item["product_id"],
                        "slug": item["slug"],
                        "name": item["pro_nombre"].strip(),
                        "quantity": quantity,
                        "price": item["pro_precio_venta"],
                    },
                )
                db.execute(
                    text(
                        "UPDATE productos SET pro_saldo_final = pro_saldo_final - :quantity "
                        "WHERE id_producto = :product_id"
                    ),
                    {"quantity": quantity, "product_id": item["product_id"]},
                )
                db.execute(
                    text(
                        "INSERT INTO apex_stock_ledger "
                        "(order_id, product_id, quantity_delta, reason, created_at) "
                        "VALUES (:order_id, :product_id, :delta, 'checkout_reservation', CURRENT_TIMESTAMP)"
                    ),
                    {
                        "order_id": new_order_id,
                        "product_id": item["product_id"],
                        "delta": -quantity,
                    },
                )
            db.execute(text("DELETE FROM detalle_carrito WHERE id_carrito = :id"), {"id": cart_id})
            db.execute(
                text("UPDATE carrito SET car_fechaultimamodif = CURRENT_TIMESTAMP WHERE id_carrito = :id"),
                {"id": cart_id},
            )
            order_id = new_order_id
    return _order_payload(db, order_id)


@router.get("/orders")
def list_my_orders(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    with db.begin():
        release_expired_reservations(db)
    rows = db.execute(
        text(
            "SELECT id, status, payment_status, total, created_at FROM apex_orders "
            "WHERE user_id = :user_id ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
        ),
        {"user_id": principal.id, "limit": limit, "offset": offset},
    ).mappings()
    return {
        "items": [
            {
                "id": row["id"].strip(),
                "status": row["status"],
                "paymentStatus": row["payment_status"],
                "totalCents": int(Decimal(row["total"]) * 100),
                "createdAt": row["created_at"],
            }
            for row in rows
        ]
    }


@router.get("/orders/{order_id}")
def get_my_order(
    order_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    with db.begin():
        release_expired_reservations(db)
    own_order = db.execute(
        text("SELECT 1 FROM apex_orders WHERE id = :id AND user_id = :user_id"),
        {"id": order_id, "user_id": principal.id},
    ).scalar_one_or_none()
    if own_order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return _order_payload(db, order_id)


@router.post("/orders/{order_id}/cancel")
def cancel_my_order(
    order_id: str,
    principal: Principal = Depends(current_principal),
    db: Session = Depends(get_db),
) -> dict:
    with db.begin():
        release_expired_reservations(db)
        order = db.execute(
            text(
                "SELECT status FROM apex_orders WHERE id = :id AND user_id = :user_id FOR UPDATE"
            ),
            {"id": order_id, "user_id": principal.id},
        ).scalar_one_or_none()
        if order is None:
            raise HTTPException(status_code=404, detail="Order not found")
        if order != "AWAITING_DEMO_PAYMENT":
            raise HTTPException(status_code=409, detail="Only pending demo orders can be cancelled")
        lines = db.execute(
            text(
                "SELECT product_id, quantity FROM apex_order_lines "
                "WHERE order_id = :id ORDER BY product_id"
            ),
            {"id": order_id},
        ).mappings().all()
        for line in lines:
            db.execute(
                text(
                    "UPDATE productos SET pro_saldo_final = pro_saldo_final + :quantity "
                    "WHERE id_producto = :product_id"
                ),
                line,
            )
            db.execute(
                text(
                    "INSERT INTO apex_stock_ledger "
                    "(order_id, product_id, quantity_delta, reason, created_at) "
                    "VALUES (:order_id, :product_id, :delta, 'checkout_cancel', CURRENT_TIMESTAMP)"
                ),
                {
                    "order_id": order_id,
                    "product_id": line["product_id"],
                    "delta": line["quantity"],
                },
            )
        db.execute(
            text("UPDATE apex_orders SET status = 'CANCELLED' WHERE id = :id"),
            {"id": order_id},
        )
    return _order_payload(db, order_id)
