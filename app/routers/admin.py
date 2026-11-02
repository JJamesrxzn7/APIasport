from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.routers.checkout import release_expired_reservations
from app.security import Principal, authorize_permission, require_admin

router = APIRouter(prefix="/admin", tags=["administration"])

RESOURCES = {
    "products": (
        "SELECT m.slug AS id, p.id_producto AS product_id, p.pro_nombre AS name, "
        "p.pro_descripcioncorta AS description, p.pro_material AS material, p.pro_talla AS size, "
        "p.pro_color AS color, p.pro_garantia AS warranty, p.id_percha AS shelf_id, "
        "p.id_categoria AS category_id, c.cat_nombre AS category, p.id_marca AS brand_id, "
        "b.mar_nombre AS brand, p.pro_um_compra AS purchase_unit_id, "
        "p.pro_um_venta AS sale_unit_id, p.pro_valor_compra AS cost, "
        "p.pro_precio_venta AS price, p.pro_saldo_inicial AS opening_stock, "
        "p.pro_qty_ingresos AS received_stock, p.pro_qty_egresos AS issued_stock, "
        "p.pro_qty_ajustes AS adjusted_stock, p.pro_saldo_final AS stock, "
        "p.estado_prod AS status, m.category_slug, m.image_url "
        "FROM productos p JOIN apex_product_catalog m ON m.product_id = p.id_producto "
        "JOIN categorias c ON c.id_categoria = p.id_categoria "
        "JOIN marcas b ON b.id_marca = p.id_marca",
        "name",
    ),
    "inventory": (
        "SELECT m.slug AS id, p.id_producto AS product_id, p.pro_nombre AS name, "
        "p.id_percha AS shelf_id, p.pro_saldo_inicial AS opening_stock, "
        "p.pro_qty_ingresos AS received_stock, p.pro_qty_egresos AS issued_stock, "
        "p.pro_qty_ajustes AS adjusted_stock, p.pro_saldo_final AS stock, "
        "COALESCE(pol.minimum_stock, 0) AS minimum_stock, "
        "CASE WHEN p.pro_saldo_final <= COALESCE(pol.minimum_stock, 0) THEN 'LOW' "
        "WHEN p.pro_saldo_final <= COALESCE(pol.minimum_stock, 0) * 2 THEN 'NEAR_MINIMUM' "
        "ELSE 'HEALTHY' END AS stock_status "
        "FROM productos p JOIN apex_product_catalog m ON m.product_id = p.id_producto "
        "LEFT JOIN apex_inventory_policy pol ON pol.product_id = p.id_producto",
        "name",
    ),
    "purchases": (
        "SELECT co.id_compra AS id, co.id_proveedor AS supplier_id, "
        "trim(pr.prv_nombre) AS supplier_name, trim(pr.prv_ruc_ced) AS supplier_identification, "
        "co.oc_fecha AS created_at, "
        "co.oc_fechaentrega AS delivery_at, co.oc_subtotal AS subtotal, "
        "co.oc_iva AS tax, co.oc_total AS total, co.estado_oc AS status "
        "FROM compras co JOIN proveedores pr ON pr.id_proveedor = co.id_proveedor",
        "created_at DESC",
    ),
    "suppliers": (
        "SELECT id_proveedor AS id, prv_nombre AS name, prv_ruc_ced AS identification, "
        "prv_telefono AS phone, prv_celular AS mobile, prv_mail AS email, "
        "prv_direccion AS address, p.id_ciudad AS city_id, trim(c.ciu_descripcion) AS city, "
        "estado_prv AS status FROM proveedores p JOIN ciudades c ON c.id_ciudad = p.id_ciudad",
        "name",
    ),
    "receipts": (
        "SELECT r.id_recibo AS id, r.id_compra AS purchase_id, trim(pr.prv_nombre) AS supplier_name, "
        "r.rec_descripcion AS description, r.rec_fechahora AS created_at, "
        "r.rec_num_produc AS product_count, r.estado_rec AS status "
        "FROM recepciones r JOIN compras co ON co.id_compra = r.id_compra "
        "JOIN proveedores pr ON pr.id_proveedor = co.id_proveedor",
        "created_at DESC",
    ),
    "sales": (
        "SELECT f.id_factura AS id, f.id_cliente AS customer_id, trim(c.cli_nombre) AS customer_name, "
        "f.id_usuario AS operator_id, trim(u.usr_nombre || ' ' || u.usr_apellido) AS operator_name, "
        "f.fac_fecha AS created_at, f.fac_formapago AS payment_method, "
        "f.fac_subtotal AS subtotal, f.fac_descuento AS discount, f.fac_iva AS tax, "
        "f.fac_total AS total, f.estado_fac AS status FROM facturas f "
        "JOIN clientes c ON c.id_cliente = f.id_cliente "
        "JOIN usuarios u ON u.id_usuario = f.id_usuario",
        "created_at DESC",
    ),
    "invoices": (
        "SELECT f.id_factura AS id, f.id_cliente AS customer_id, trim(c.cli_nombre) AS customer_name, "
        "f.id_usuario AS operator_id, trim(u.usr_nombre || ' ' || u.usr_apellido) AS issuer_name, "
        "f.fac_descripcion AS description, f.fac_fecha AS created_at, "
        "f.fac_formapago AS payment_method, f.fac_subtotal AS subtotal, "
        "f.fac_descuento AS discount, f.fac_iva AS tax, f.fac_total AS total, "
        "f.estado_fac AS status FROM facturas f "
        "JOIN clientes c ON c.id_cliente = f.id_cliente "
        "JOIN usuarios u ON u.id_usuario = f.id_usuario",
        "created_at DESC",
    ),
    "deliveries": (
        "SELECT e.id_entrega AS id, e.id_factura AS invoice_id, "
        "trim(c.cli_nombre) AS recipient_name, c.cli_direccion AS delivery_address, "
        "e.ent_descripcion AS description, e.ent_fechahora AS created_at, "
        "e.ent_num_produc AS product_count, e.estado_ent AS status "
        "FROM entregas e JOIN facturas f ON f.id_factura = e.id_factura "
        "JOIN clientes c ON c.id_cliente = f.id_cliente",
        "created_at DESC",
    ),
    "customers": (
        "SELECT c.id_cliente AS id, c.id_usuario AS user_id, trim(c.cli_nombre) AS name, "
        "trim(c.cli_ruc_ced) AS identification, "
        "c.cli_telefono AS phone, c.cli_mail AS email, c.id_ciudad AS city_id, "
        "c.cli_celular AS mobile, c.cli_direccion AS address, "
        "c.cli_montomaxcredito AS credit_limit, c.cli_descuento AS discount, "
        "trim(ci.ciu_descripcion) AS city, c.estado_cli AS status "
        "FROM clientes c JOIN ciudades ci ON ci.id_ciudad = c.id_ciudad",
        "name",
    ),
    "users": (
        "SELECT u.id_usuario AS id, usr_username AS username, trim(usr_nombre) AS first_name, "
        "usr_apellido AS last_name, usr_mail AS email, usr_telefono AS phone, "
        "usr_ultimoacceso AS last_login, estado_usr AS status, "
        "COALESCE((SELECT string_agg(trim(r.rol_nombre), ', ' ORDER BY trim(r.rol_nombre)) "
        "FROM usuariosxroles ur JOIN roles r ON r.id_rol = ur.id_rol "
        "WHERE ur.id_usuario = u.id_usuario AND ur.estado_uxr = 'ACT' "
        "AND r.estado_rol = 'ACT'), '') AS roles FROM usuarios u",
        "last_name, first_name",
    ),
    "roles": (
        "SELECT id_rol AS id, rol_nombre AS name, rol_descripcion AS description, "
        "estado_rol AS status, COALESCE((SELECT string_agg(p.prm_modulo || ':' || p.prm_accion, "
        "', ' ORDER BY p.prm_modulo, p.prm_accion) FROM rolesxpermisos rp "
        "JOIN permisos p ON p.id_permiso = rp.id_permiso WHERE rp.id_rol = r.id_rol "
        "AND rp.estado_rxp = 'ACT' AND p.estado_prm = 'ACT'), '') AS permissions FROM roles r",
        "name",
    ),
    "permissions": (
        "SELECT id_permiso AS id, prm_modulo AS module, prm_accion AS action, "
        "prm_descripcion AS description, estado_prm AS status FROM permisos",
        "module, action",
    ),
    "audit": (
        "SELECT l.id_log AS id, l.id_usuario AS user_id, "
        "COALESCE(trim(u.usr_nombre || ' ' || u.usr_apellido), l.id_usuario) AS actor, "
        "l.log_fechahora AS created_at, l.log_tablaafectada AS table_name, "
        "l.log_accion AS action, l.log_ip AS ip, "
        "l.log_valoranterior AS old_value, l.log_valornuevo AS new_value "
        "FROM log_auditoria l LEFT JOIN usuarios u ON u.id_usuario = l.id_usuario",
        "created_at DESC",
    ),
}

RESOURCE_MODULES = {
    "products": "products",
    "inventory": "inventory",
    "purchases": "purchases",
    "suppliers": "suppliers",
    "receipts": "receipts",
    "sales": "sales",
    "invoices": "invoices",
    "deliveries": "deliveries",
    "customers": "customers",
    "users": "users",
    "roles": "roles",
    "permissions": "permissions",
    "audit": "audit",
    "orders": "orders",
}

DATE_FILTER_RESOURCES = {
    "purchases", "receipts", "sales", "invoices", "deliveries", "audit", "orders"
}


def _audit(db: Session, principal: Principal, table_name: str, action: str, new_value: dict) -> None:
    write_audit(db, principal.id, table_name, action, new_value)


@router.get("/summary")
def summary(
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    authorize_permission(db, principal, "dashboard", "read")
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
    result = {key: db.execute(text(query)).scalar_one() for key, query in counts.items()}
    period_end = date_to or date.today()
    period_start = date_from or period_end - timedelta(days=6)
    if period_start > period_end:
        raise HTTPException(status_code=422, detail="'from' must be on or before 'to'")
    if (period_end - period_start).days > 365:
        raise HTTPException(status_code=422, detail="Summary period cannot exceed 366 days")
    date_params = {"date_from": period_start, "date_to": period_end}
    period_sales = db.execute(
        text(
            "SELECT COUNT(*) AS count, COALESCE(SUM(fac_total), 0) AS revenue, "
            "COALESCE(AVG(fac_total), 0) AS average "
            "FROM facturas WHERE estado_fac = 'PAG' "
            "AND fac_fecha >= :date_from AND fac_fecha < :date_to + INTERVAL '1 day'"
        ),
        date_params,
    ).mappings().one()
    sales_by_day = {
        row["date"]: row
        for row in db.execute(
            text(
                "SELECT fac_fecha::date AS date, COUNT(*) AS count, SUM(fac_total) AS revenue "
                "FROM facturas WHERE estado_fac = 'PAG' "
                "AND fac_fecha >= :date_from AND fac_fecha < :date_to + INTERVAL '1 day' "
                "GROUP BY fac_fecha::date ORDER BY date"
            ),
            date_params,
        ).mappings()
    }
    trend = []
    current_day = period_start
    while current_day <= period_end:
        row = sales_by_day.get(current_day)
        trend.append(
            {
                "date": current_day.isoformat(),
                "count": row["count"] if row else 0,
                "revenueCents": int(Decimal(row["revenue"]) * 100) if row else 0,
            }
        )
        current_day += timedelta(days=1)
    low_stock = db.execute(
        text(
            "SELECT m.slug AS id, p.pro_nombre AS name, p.pro_saldo_final AS stock, "
            "COALESCE(pol.minimum_stock, 0) AS minimum_stock "
            "FROM productos p JOIN apex_product_catalog m ON m.product_id = p.id_producto "
            "LEFT JOIN apex_inventory_policy pol ON pol.product_id = p.id_producto "
            "WHERE p.estado_prod = 'ACT' "
            "AND p.pro_saldo_final <= COALESCE(pol.minimum_stock, 0) "
            "ORDER BY p.pro_saldo_final, p.pro_nombre LIMIT 10"
        )
    ).mappings()
    recent_sales = db.execute(
        text(
            "SELECT f.id_factura AS id, trim(c.cli_nombre) AS customer, "
            "f.fac_fecha AS created_at, f.fac_total AS total, f.estado_fac AS status "
            "FROM facturas f JOIN clientes c ON c.id_cliente = f.id_cliente "
            "WHERE f.estado_fac = 'PAG' ORDER BY f.fac_fecha DESC LIMIT 5"
        )
    ).mappings()
    recent_activity = db.execute(
        text(
            "SELECT l.id_log AS id, "
            "COALESCE(trim(u.usr_nombre || ' ' || u.usr_apellido), l.id_usuario) AS actor, "
            "l.log_tablaafectada AS table_name, l.log_accion AS action, "
            "l.log_fechahora AS created_at "
            "FROM log_auditoria l LEFT JOIN usuarios u ON u.id_usuario = l.id_usuario "
            "ORDER BY l.log_fechahora DESC LIMIT 10"
        )
    ).mappings()
    result.update(
        {
            "salesCount": period_sales["count"],
            "salesRevenueCents": int(Decimal(period_sales["revenue"]) * 100),
            "averageSaleCents": int(Decimal(period_sales["average"]) * 100),
            "period": {"from": period_start.isoformat(), "to": period_end.isoformat()},
            "salesTrend": trend,
            "lowStockItems": [dict(row) for row in low_stock],
            "recentSales": [
                {**dict(row), "totalCents": int(Decimal(row["total"]) * 100)}
                for row in recent_sales
            ],
            "recentActivity": [dict(row) for row in recent_activity],
        }
    )
    return result


@router.post("/orders/release-expired")
def release_expired_orders(
    principal: Principal = Depends(require_admin), db: Session = Depends(get_db)
) -> dict:
    authorize_permission(db, principal, "orders", "write")
    with db.begin():
        released = release_expired_reservations(db)
    return {"releasedReservations": released}


@router.get("/resources/{resource}")
def list_resource(
    resource: str,
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=100),
    status_filter: str | None = Query(default=None, alias="status", min_length=1, max_length=32),
    date_from: date | None = Query(default=None, alias="from"),
    date_to: date | None = Query(default=None, alias="to"),
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    module = RESOURCE_MODULES.get(resource)
    if module is None:
        raise HTTPException(status_code=404, detail="Admin resource not found")
    authorize_permission(db, principal, module, "read")
    if (date_from or date_to) and resource not in DATE_FILTER_RESOURCES:
        raise HTTPException(status_code=422, detail="Date filtering is not available for this resource")
    if date_from and date_to and date_from > date_to:
        raise HTTPException(status_code=422, detail="'from' must be on or before 'to'")
    if resource == "orders":
        where = []
        params: dict = {"limit": limit, "offset": offset}
        if search:
            where.append(
                "(id ILIKE :search OR status ILIKE :search OR delivery_city ILIKE :search)"
            )
            params["search"] = f"%{search.strip()}%"
        if status_filter:
            where.append("status = :status")
            params["status"] = status_filter.strip().upper()
        if date_from:
            where.append("created_at >= CAST(:date_from AS TIMESTAMP)")
            params["date_from"] = date_from
        if date_to:
            where.append("created_at < CAST(:date_to AS TIMESTAMP) + INTERVAL '1 day'")
            params["date_to"] = date_to
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        rows = db.execute(
            text(
                "SELECT o.id, o.user_id, trim(u.usr_nombre || ' ' || u.usr_apellido) AS user_name, "
                "o.client_id, trim(c.cli_nombre) AS customer_name, o.status, o.payment_status, "
                "o.subtotal, o.delivery_fee, o.total, o.delivery_address, o.delivery_phone, "
                "o.delivery_reference, o.delivery_city, o.delivery_method, o.created_at, o.expires_at "
                "FROM apex_orders o JOIN usuarios u ON u.id_usuario = o.user_id "
                "JOIN clientes c ON c.id_cliente = o.client_id"
                + where_sql + " ORDER BY created_at DESC LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        total = db.execute(
            text("SELECT count(*) FROM apex_orders" + where_sql),
            {key: value for key, value in params.items() if key not in {"limit", "offset"}},
        ).scalar_one()
    else:
        query, order_by = RESOURCES[resource]
        where = []
        params = {"limit": limit, "offset": offset}
        if search:
            where.append("to_jsonb(resource_rows)::text ILIKE :search")
            params["search"] = f"%{search.strip()}%"
        if status_filter:
            where.append("resource_rows.status = :status")
            params["status"] = status_filter.strip().upper()
        if date_from:
            where.append("resource_rows.created_at >= CAST(:date_from AS TIMESTAMP)")
            params["date_from"] = date_from
        if date_to:
            where.append("resource_rows.created_at < CAST(:date_to AS TIMESTAMP) + INTERVAL '1 day'")
            params["date_to"] = date_to
        where_sql = f" WHERE {' AND '.join(where)}" if where else ""
        rows = db.execute(
            text(
                f"SELECT * FROM ({query}) AS resource_rows{where_sql} "
                f"ORDER BY {order_by} LIMIT :limit OFFSET :offset"
            ),
            params,
        ).mappings()
        total = db.execute(
            text(
                f"SELECT count(*) FROM ({query}) AS resource_rows{where_sql}"
            ),
            {key: value for key, value in params.items() if key not in {"limit", "offset"}},
        ).scalar_one()
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
    authorize_permission(db, principal, "products", "write")
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
    authorize_permission(db, principal, "products", "write")
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
    authorize_permission(db, principal, "inventory", "write")
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
