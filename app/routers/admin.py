import json
import secrets
import string
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.routers.checkout import release_expired_reservations
from app.security import Principal, require_admin

router = APIRouter(prefix="/admin", tags=["administration"])

RESOURCES = {
    "products": (
        "SELECT m.slug AS id, p.pro_nombre AS name, p.pro_descripcioncorta AS description, "
        "c.cat_nombre AS category, b.mar_nombre AS brand, p.pro_precio_venta AS price, "
        "p.pro_saldo_final AS stock, p.estado_prod AS status "
        "FROM productos p JOIN apex_product_catalog m ON m.product_id = p.id_producto "
        "JOIN categorias c ON c.id_categoria = p.id_categoria "
        "JOIN marcas b ON b.id_marca = p.id_marca",
        "name",
    ),
    "inventory": (
        "SELECT m.slug AS id, p.pro_nombre AS name, p.pro_saldo_final AS stock, "
        "COALESCE(pol.minimum_stock, 0) AS minimum_stock "
        "FROM productos p JOIN apex_product_catalog m ON m.product_id = p.id_producto "
        "LEFT JOIN apex_inventory_policy pol ON pol.product_id = p.id_producto",
        "name",
    ),
    "purchases": (
        "SELECT id_compra AS id, id_proveedor AS supplier_id, oc_fecha AS created_at, "
        "oc_fechaentrega AS delivery_at, oc_subtotal AS subtotal, oc_iva AS tax, "
        "oc_total AS total, estado_oc AS status FROM compras",
        "created_at DESC",
    ),
    "suppliers": (
        "SELECT id_proveedor AS id, prv_nombre AS name, prv_ruc_ced AS identification, "
        "prv_telefono AS phone, prv_mail AS email, id_ciudad AS city_id, estado_prv AS status "
        "FROM proveedores",
        "name",
    ),
    "receipts": (
        "SELECT id_recibo AS id, id_compra AS purchase_id, rec_descripcion AS description, "
        "rec_fechahora AS created_at, rec_num_produc AS product_count, estado_rec AS status "
        "FROM recepciones",
        "created_at DESC",
    ),
    "sales": (
        "SELECT id_factura AS id, id_cliente AS customer_id, fac_fecha AS created_at, "
        "fac_subtotal AS subtotal, fac_iva AS tax, fac_total AS total, estado_fac AS status "
        "FROM facturas",
        "created_at DESC",
    ),
    "invoices": (
        "SELECT id_factura AS id, id_cliente AS customer_id, fac_fecha AS created_at, "
        "fac_formapago AS payment_method, fac_subtotal AS subtotal, fac_iva AS tax, "
        "fac_total AS total, estado_fac AS status FROM facturas",
        "created_at DESC",
    ),
    "deliveries": (
        "SELECT id_entrega AS id, id_factura AS invoice_id, ent_descripcion AS description, "
        "ent_fechahora AS created_at, ent_num_produc AS product_count, estado_ent AS status "
        "FROM entregas",
        "created_at DESC",
    ),
    "customers": (
        "SELECT id_cliente AS id, cli_nombre AS name, cli_ruc_ced AS identification, "
        "cli_telefono AS phone, cli_mail AS email, id_ciudad AS city_id, "
        "cli_direccion AS address, estado_cli AS status FROM clientes",
        "name",
    ),
    "users": (
        "SELECT id_usuario AS id, usr_username AS username, usr_nombre AS first_name, "
        "usr_apellido AS last_name, usr_mail AS email, usr_telefono AS phone, "
        "usr_ultimoacceso AS last_login, estado_usr AS status FROM usuarios",
        "last_name, first_name",
    ),
    "roles": (
        "SELECT id_rol AS id, rol_nombre AS name, rol_descripcion AS description, "
        "estado_rol AS status FROM roles",
        "name",
    ),
    "audit": (
        "SELECT id_log AS id, id_usuario AS user_id, log_fechahora AS created_at, "
        "log_tablaafectada AS table_name, log_accion AS action, log_ip AS ip "
        "FROM log_auditoria",
        "created_at DESC",
    ),
}


def _audit(db: Session, principal: Principal, table_name: str, action: str, new_value: dict) -> None:
    audit_id = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(15))
    db.execute(
        text(
            "INSERT INTO log_auditoria "
            "(id_log, id_usuario, log_fechahora, log_tablaafectada, log_accion, log_valornuevo) "
            "VALUES (:id, :user, CURRENT_TIMESTAMP, :table_name, :action, :value)"
        ),
        {
            "id": audit_id,
            "user": principal.id,
            "table_name": table_name,
            "action": action,
            "value": json.dumps(new_value, ensure_ascii=False),
        },
    )


@router.get("/summary")
def summary(
    principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    counts = {
        "products": "SELECT count(*) FROM productos WHERE estado_prod = 'ACT'",
        "inventory": (
            "SELECT count(*) FROM productos p LEFT JOIN apex_inventory_policy pol "
            "ON pol.product_id = p.id_producto WHERE p.estado_prod = 'ACT' "
            "AND p.pro_saldo_final <= COALESCE(pol.minimum_stock, 0)"
        ),
        "purchases": "SELECT count(*) FROM compras",
        "sales": "SELECT count(*) FROM facturas",
        "customers": "SELECT count(*) FROM clientes",
        "users": "SELECT count(*) FROM usuarios WHERE estado_usr = 'ACT'",
        "pendingOrders": "SELECT count(*) FROM apex_orders WHERE status = 'AWAITING_DEMO_PAYMENT'",
    }
    return {key: db.execute(text(query)).scalar_one() for key, query in counts.items()}


@router.post("/orders/release-expired")
def release_expired_orders(
    principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    with db.begin():
        released = release_expired_reservations(db)
    return {"releasedReservations": released}


@router.get("/resources/{resource}")
def list_resource(
    resource: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if resource == "orders":
        rows = db.execute(
            text(
                "SELECT id, user_id, client_id, status, payment_status, subtotal, delivery_fee, "
                "total, delivery_city, delivery_method, created_at, expires_at FROM apex_orders "
                "ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
            ),
            {"limit": limit, "offset": offset},
        ).mappings()
        total = db.execute(text("SELECT count(*) FROM apex_orders")).scalar_one()
    else:
        definition = RESOURCES.get(resource)
        if definition is None:
            raise HTTPException(status_code=404, detail="Admin resource not found")
        query, order_by = definition
        rows = db.execute(
            text(f"SELECT * FROM ({query}) AS resource_rows ORDER BY {order_by} LIMIT :limit OFFSET :offset"),
            {"limit": limit, "offset": offset},
        ).mappings()
        total = db.execute(text(f"SELECT count(*) FROM ({query}) AS resource_count")).scalar_one()
    return {"items": [dict(row) for row in rows], "total": total, "limit": limit, "offset": offset}


class ProductUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=255)
    price_cents: int | None = Field(default=None, ge=0, le=9_999_999)
    active: bool | None = None


@router.patch("/products/{slug}")
def update_product(
    slug: str,
    body: ProductUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No product changes supplied")
    if any(value is None for key, value in changes.items() if key != "description"):
        raise HTTPException(status_code=422, detail="Non-nullable product fields cannot be null")
    product_id = db.execute(
        text("SELECT product_id FROM apex_product_catalog WHERE slug = :slug"),
        {"slug": slug},
    ).scalar_one_or_none()
    if product_id is None:
        raise HTTPException(status_code=404, detail="Product not found")
    assignments = []
    params: dict = {"id": product_id}
    for key, value in changes.items():
        if key == "name":
            assignments.append("pro_nombre = :name")
            params["name"] = value
        elif key == "description":
            assignments.append("pro_descripcioncorta = :description")
            params["description"] = value
        elif key == "price_cents":
            assignments.append("pro_precio_venta = :price")
            params["price"] = Decimal(value) / 100
        elif key == "active":
            assignments.append("estado_prod = :status")
            params["status"] = "ACT" if value else "INA"
    try:
        db.execute(text(f"UPDATE productos SET {', '.join(assignments)} WHERE id_producto = :id"), params)
        _audit(db, principal, "PRODUCTOS", "UPDATE", {"slug": slug, **changes})
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product update conflicts with database constraints") from exc
    return {"id": slug, "updated": changes}


class CatalogMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str = Field(min_length=1, max_length=10)
    slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)
    category_slug: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)
    image_url: str = Field(min_length=1, max_length=255)


@router.put("/catalog/mappings", status_code=status.HTTP_200_OK)
def set_catalog_mapping(
    body: CatalogMapping,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    if not body.image_url.startswith(("https://", "images/")):
        raise HTTPException(status_code=422, detail="image_url must be a relative image path or HTTPS URL")
    try:
        db.execute(
            text(
                "INSERT INTO apex_product_catalog (product_id, slug, category_slug, image_url) "
                "VALUES (:product_id, :slug, :category_slug, :image_url) "
                "ON CONFLICT (product_id) DO UPDATE SET slug = EXCLUDED.slug, "
                "category_slug = EXCLUDED.category_slug, image_url = EXCLUDED.image_url"
            ),
            body.model_dump(),
        )
        _audit(db, principal, "APEX_PRODUCT_CATALOG", "UPSERT", body.model_dump())
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="Product mapping conflicts with catalog data") from exc
    return {"id": body.slug, "productId": body.product_id}


class InventoryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    minimum_stock: int = Field(ge=0, le=1_000_000)


@router.put("/inventory/{slug}/minimum")
def set_minimum_stock(
    slug: str,
    body: InventoryPolicy,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    product_id = db.execute(
        text("SELECT product_id FROM apex_product_catalog WHERE slug = :slug"),
        {"slug": slug},
    ).scalar_one_or_none()
    if product_id is None:
        raise HTTPException(status_code=404, detail="Product not found")
    db.execute(
        text(
            "INSERT INTO apex_inventory_policy (product_id, minimum_stock) VALUES (:id, :minimum) "
            "ON CONFLICT (product_id) DO UPDATE SET minimum_stock = EXCLUDED.minimum_stock"
        ),
        {"id": product_id, "minimum": body.minimum_stock},
    )
    _audit(db, principal, "APEX_INVENTORY_POLICY", "UPSERT", {"slug": slug, **body.model_dump()})
    db.commit()
    return {"productId": slug, **body.model_dump()}
