import re
import secrets
import string
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.audit import write_audit
from app.database import get_db
from app.routers.admin import RESOURCE_MODULES, RESOURCES
from app.security import Principal, authorize_permission, require_admin

router = APIRouter(prefix="/admin", tags=["administration"])

IdText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
NameText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
PositiveQuantity = Annotated[int, Field(gt=0, le=1_000_000)]
Money = Annotated[Decimal, Field(ge=0, le=Decimal("99999.99"), max_digits=7, decimal_places=2)]
RoleId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]
PermissionId = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=10)]


class AdminBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProductImage(AdminBody):
    url: Annotated[str, Field(min_length=1, max_length=255)]
    order: Annotated[int, Field(ge=1, le=100)] = 1


class ProductCreate(AdminBody):
    product_id: Annotated[str, Field(min_length=1, max_length=10)] | None = None
    slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
    category_slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)]
    image_url: Annotated[str, Field(min_length=1, max_length=255)]
    shelf_id: Annotated[str, Field(min_length=1, max_length=10)]
    category_id: Annotated[str, Field(min_length=1, max_length=10)]
    brand_id: Annotated[str, Field(min_length=1, max_length=10)]
    purchase_unit_id: Annotated[str, Field(min_length=1, max_length=3)]
    sale_unit_id: Annotated[str, Field(min_length=1, max_length=3)]
    name: NameText
    description: Annotated[str, Field(max_length=255)] = ""
    material: Annotated[str, Field(max_length=100)] | None = None
    size: Annotated[str, Field(max_length=20)] | None = None
    color: Annotated[str, Field(max_length=50)] | None = None
    warranty: Annotated[str, Field(max_length=100)] | None = None
    cost: Money
    price: Money
    opening_stock: Annotated[int, Field(ge=0, le=2_147_483_647)] = 0
    images: Annotated[list[ProductImage], Field(max_length=20)] = Field(default_factory=list)


class ProductCatalogUpdate(AdminBody):
    shelf_id: Annotated[str, Field(min_length=1, max_length=10)] | None = None
    category_id: Annotated[str, Field(min_length=1, max_length=10)] | None = None
    brand_id: Annotated[str, Field(min_length=1, max_length=10)] | None = None
    purchase_unit_id: Annotated[str, Field(min_length=1, max_length=3)] | None = None
    sale_unit_id: Annotated[str, Field(min_length=1, max_length=3)] | None = None
    name: NameText | None = None
    description: Annotated[str, Field(max_length=255)] | None = None
    material: Annotated[str, Field(max_length=100)] | None = None
    size: Annotated[str, Field(max_length=20)] | None = None
    color: Annotated[str, Field(max_length=50)] | None = None
    warranty: Annotated[str, Field(max_length=100)] | None = None
    cost: Money | None = None
    price: Money | None = None
    category_slug: Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)] | None = None
    image_url: Annotated[str, Field(min_length=1, max_length=255)] | None = None
    images: Annotated[list[ProductImage], Field(max_length=20)] | None = None
    active: bool | None = None


class SupplierCreate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    identification: Annotated[str, Field(pattern=r"^\d{13}$")]
    phone: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    mobile: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    email: Annotated[str, Field(max_length=60)] | None = None
    city_id: Annotated[str, Field(min_length=1, max_length=3)]
    address: Annotated[str, Field(max_length=60)] | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return _normalize_email(value)


class SupplierUpdate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None = None
    identification: Annotated[str, Field(pattern=r"^\d{13}$")] | None = None
    phone: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    mobile: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    email: Annotated[str, Field(max_length=60)] | None = None
    city_id: Annotated[str, Field(min_length=1, max_length=3)] | None = None
    address: Annotated[str, Field(max_length=60)] | None = None
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return _normalize_email(value)


class CustomerCreate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    identification: Annotated[str, Field(pattern=r"^\d{10}(?:\d{3})?$")]
    phone: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    mobile: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    email: Annotated[str, Field(max_length=60)] | None = None
    city_id: Annotated[str, Field(min_length=1, max_length=3)]
    address: Annotated[str, Field(max_length=60)] | None = None
    credit_limit: Annotated[Decimal, Field(ge=0, le=Decimal("99999999.99"), max_digits=10, decimal_places=2)] | None = None
    discount: Annotated[Decimal, Field(ge=0, le=Decimal("999.99"), max_digits=5, decimal_places=2)] | None = None
    user_id: Annotated[str, Field(min_length=1, max_length=16)] | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return _normalize_email(value)


class CustomerUpdate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)] | None = None
    identification: Annotated[str, Field(pattern=r"^\d{10}(?:\d{3})?$")] | None = None
    phone: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    mobile: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    email: Annotated[str, Field(max_length=60)] | None = None
    city_id: Annotated[str, Field(min_length=1, max_length=3)] | None = None
    address: Annotated[str, Field(max_length=60)] | None = None
    credit_limit: Annotated[Decimal, Field(ge=0, le=Decimal("99999999.99"), max_digits=10, decimal_places=2)] | None = None
    discount: Annotated[Decimal, Field(ge=0, le=Decimal("999.99"), max_digits=5, decimal_places=2)] | None = None
    user_id: Annotated[str, Field(min_length=1, max_length=16)] | None = None
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return _normalize_email(value)


class PurchaseLine(AdminBody):
    product_slug: IdText
    quantity: PositiveQuantity
    unit_price: Money


class PurchaseCreate(AdminBody):
    supplier_id: Annotated[str, Field(min_length=1, max_length=10)]
    created_at: datetime
    delivery_at: datetime | None = None
    items: Annotated[list[PurchaseLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_products(self):
        ids = [line.product_slug for line in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product can appear only once in a purchase")
        return self


class ReceiptLine(AdminBody):
    product_slug: IdText
    received_quantity: Annotated[int, Field(ge=0, le=1_000_000)]
    reason: Annotated[str, Field(max_length=60)] | None = None


class ReceiptCreate(AdminBody):
    description: Annotated[str, Field(max_length=30)] | None = None
    created_at: datetime


class ReceiptLinesRequest(AdminBody):
    items: Annotated[list[ReceiptLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_products(self):
        ids = [line.product_slug for line in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product can appear only once in a receipt")
        return self


class DeliveryLine(AdminBody):
    product_slug: IdText
    delivered_quantity: PositiveQuantity
    reason: Annotated[str, Field(max_length=60)] | None = None


class DeliveryCompletionRequest(AdminBody):
    items: Annotated[list[DeliveryLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_products(self):
        ids = [line.product_slug for line in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product can appear only once on a delivery")
        return self


class AdjustmentLine(AdminBody):
    product_slug: IdText
    delta: Annotated[int, Field(ge=-1_000_000, le=1_000_000)]

    @field_validator("delta")
    @classmethod
    def reject_zero_delta(cls, value: int) -> int:
        if value == 0:
            raise ValueError("Adjustment delta cannot be zero")
        return value


class AdjustmentCreate(AdminBody):
    description: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=30)]
    created_at: datetime
    items: Annotated[list[AdjustmentLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_products(self):
        ids = [line.product_slug for line in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product can appear only once in an adjustment")
        return self


class InvoiceLine(AdminBody):
    product_slug: IdText
    quantity: PositiveQuantity


class InvoiceCreate(AdminBody):
    customer_id: Annotated[str, Field(min_length=1, max_length=10)]
    payment_method: Literal["Tarjeta", "Efectivo", "Transferencia"]
    description: Annotated[str, Field(max_length=60)] | None = None
    created_at: datetime
    discount: Annotated[Decimal, Field(ge=0, le=Decimal("99999.99"), max_digits=7, decimal_places=2)] = Decimal("0.00")
    items: Annotated[list[InvoiceLine], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def unique_products(self):
        ids = [line.product_slug for line in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("Each product can appear only once on an invoice")
        return self


class RoleCreate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
    description: Annotated[str, Field(max_length=100)] | None = None


class RoleUpdate(AdminBody):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    description: Annotated[str, Field(max_length=100)] | None = None
    active: bool | None = None


class PermissionAssignment(AdminBody):
    permission_ids: Annotated[list[PermissionId], Field(max_length=100)]


class UserUpdate(AdminBody):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)] | None = None
    first_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    last_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)] | None = None
    email: Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=50)] | None = None
    phone: Annotated[str, Field(pattern=r"^\d{10}$")] | None = None
    active: bool | None = None

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str | None) -> str | None:
        if value is not None:
            normalized = value.lower()
            if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
                raise ValueError("Invalid email address")
            return normalized
        return value


class RoleAssignment(AdminBody):
    role_ids: Annotated[list[RoleId], Field(max_length=50)]


def _new_id(length: int) -> str:
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(length))


def _normalize_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise ValueError("Invalid email address")
    return normalized


def _check_permission(db: Session, principal: Principal, module: str, action: str) -> None:
    authorize_permission(db, principal, module, action)


def _slug_product(db: Session, slug: str) -> dict:
    row = db.execute(
        text(
            "SELECT m.product_id, p.pro_nombre AS name, p.pro_precio_venta AS price, "
            "p.pro_valor_compra AS cost, p.pro_saldo_final AS stock "
            "FROM apex_product_catalog m JOIN productos p ON p.id_producto = m.product_id "
            "WHERE m.slug = :slug AND p.estado_prod = 'ACT' FOR UPDATE OF p"
        ),
        {"slug": slug},
    ).mappings().one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Active product not found: {slug}")
    return dict(row)


def _record_integrity_conflict(exc: IntegrityError, message: str) -> HTTPException:
    return HTTPException(status_code=409, detail=message)


def _audit(
    db: Session,
    principal: Principal,
    table_name: str,
    action: str,
    payload: dict,
) -> None:
    write_audit(db, principal.id, table_name, action, payload)


def _convert_amount(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"))


@router.get("/lookups/{lookup}")
def get_admin_lookup(
    lookup: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> list[dict]:
    definitions = {
        "cities": ("suppliers", "SELECT id_ciudad AS id, trim(ciu_descripcion) AS name FROM ciudades ORDER BY ciu_descripcion"),
        "categories": ("products", "SELECT id_categoria AS id, cat_nombre AS name FROM categorias WHERE estado_cat = 'ACT' ORDER BY cat_nombre"),
        "brands": ("products", "SELECT id_marca AS id, mar_nombre AS name FROM marcas WHERE estado_mar = 'ACT' ORDER BY mar_nombre"),
        "units": ("products", "SELECT id_unidad_medida AS id, trim(um_descripcion) AS name FROM unidades_medidas ORDER BY um_descripcion"),
        "shelves": ("products", "SELECT id_percha AS id, trim(pch_letra) || '-' || pch_nivel AS name FROM perchas WHERE estado_pch = 'ACT' ORDER BY pch_letra, pch_nivel"),
        "products": ("products", "SELECT m.slug AS id, p.pro_nombre AS name, p.pro_saldo_final AS stock, p.pro_precio_venta AS price FROM apex_product_catalog m JOIN productos p ON p.id_producto = m.product_id WHERE p.estado_prod = 'ACT' ORDER BY p.pro_nombre"),
        "customers": ("customers", "SELECT id_cliente AS id, trim(cli_nombre) AS name FROM clientes WHERE estado_cli = 'ACT' ORDER BY cli_nombre"),
        "suppliers": ("suppliers", "SELECT id_proveedor AS id, trim(prv_nombre) AS name FROM proveedores WHERE estado_prv = 'ACT' ORDER BY prv_nombre"),
        "users": ("users", "SELECT id_usuario AS id, trim(usr_nombre || ' ' || usr_apellido) AS name FROM usuarios WHERE estado_usr = 'ACT' ORDER BY usr_apellido, usr_nombre"),
        "roles": ("roles", "SELECT id_rol AS id, rol_nombre AS name FROM roles WHERE estado_rol = 'ACT' ORDER BY rol_nombre"),
        "permissions": ("permissions", "SELECT id_permiso AS id, prm_modulo AS module, prm_accion AS action, prm_descripcion AS description FROM permisos WHERE estado_prm = 'ACT' ORDER BY prm_modulo, prm_accion"),
        "purchases": ("purchases", "SELECT id_compra AS id, id_compra AS name FROM compras ORDER BY oc_fecha DESC"),
        "invoices": ("invoices", "SELECT id_factura AS id, id_factura AS name FROM facturas ORDER BY fac_fecha DESC"),
    }
    definition = definitions.get(lookup)
    if definition is None:
        raise HTTPException(status_code=404, detail="Admin lookup not found")
    module, query = definition
    _check_permission(db, principal, module, "read")
    return [dict(row) for row in db.execute(text(query)).mappings()]


@router.get("/resources/{resource}/{resource_id}")
def get_admin_resource_detail(
    resource: str,
    resource_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    module = RESOURCE_MODULES.get(resource)
    if module is None:
        raise HTTPException(status_code=404, detail="Admin resource not found")
    _check_permission(db, principal, module, "read")
    if resource == "orders":
        item = db.execute(
            text(
                "SELECT o.id, o.user_id, trim(u.usr_nombre || ' ' || u.usr_apellido) AS user_name, "
                "o.client_id, trim(c.cli_nombre) AS customer_name, o.status, o.payment_status, "
                "o.payment_method, o.subtotal, o.delivery_fee, o.total, o.delivery_address, "
                "o.delivery_city, o.delivery_phone, o.delivery_reference, o.delivery_method, "
                "o.created_at, o.expires_at FROM apex_orders o "
                "JOIN usuarios u ON u.id_usuario = o.user_id "
                "JOIN clientes c ON c.id_cliente = o.client_id WHERE o.id = :id"
            ),
            {"id": resource_id},
        ).mappings().one_or_none()
        if item is None:
            raise HTTPException(status_code=404, detail="Admin resource record not found")
        lines = db.execute(
            text(
                "SELECT product_slug AS product_id, product_name, quantity, unit_price "
                "FROM apex_order_lines WHERE order_id = :id ORDER BY product_name"
            ),
            {"id": resource_id},
        ).mappings()
        return {"item": dict(item), "lines": [dict(row) for row in lines]}

    query = RESOURCES[resource][0]
    item = db.execute(
        text(f"SELECT * FROM ({query}) AS resource_rows WHERE id = :id"),
        {"id": resource_id},
    ).mappings().one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="Admin resource record not found")
    result: dict = {"item": dict(item)}
    if resource == "products":
        product_id = item["product_id"]
        result["photos"] = [
            dict(row)
            for row in db.execute(
                text(
                    "SELECT id_foto AS id, fot_url AS url, fot_orden AS ordering "
                    "FROM producto_fotografias WHERE id_producto = :id AND estado_fot = 'ACT' "
                    "ORDER BY fot_orden, id_foto"
                ),
                {"id": product_id},
            ).mappings()
        ]
    elif resource == "inventory":
        product_id = item["product_id"]
        result["movements"] = [
            dict(row)
            for row in db.execute(
                text(
                    "SELECT actor_id, quantity_delta, reason, reference_id, created_at "
                    "FROM apex_inventory_movements WHERE product_id = :id "
                    "UNION ALL "
                    "SELECT NULL AS actor_id, quantity_delta, reason, order_id AS reference_id, created_at "
                    "FROM apex_stock_ledger WHERE product_id = :id "
                    "ORDER BY created_at DESC LIMIT 100"
                ),
                {"id": product_id},
            ).mappings()
        ]
    elif resource == "purchases":
        lines_query = (
            "SELECT m.slug AS product_id, p.pro_nombre AS product_name, d.pxo_cantidad AS quantity, "
            "d.pxo_valor AS unit_price, d.pxo_subtotal AS subtotal, d.estado_pxoc AS status "
            "FROM proxoc d JOIN productos p ON p.id_producto = d.id_producto "
            "LEFT JOIN apex_product_catalog m ON m.product_id = d.id_producto "
            "WHERE d.id_compra = :id ORDER BY p.pro_nombre"
        )
        result["lines"] = [dict(row) for row in db.execute(text(lines_query), {"id": resource_id}).mappings()]
    elif resource == "receipts":
        lines_query = (
            "SELECT m.slug AS product_id, p.pro_nombre AS product_name, d.pxr_cantidad AS expected_quantity, "
            "d.pxr_qty_recibida AS received_quantity, d.pxr_motivo AS reason, d.estado_pxrec AS status "
            "FROM proxrec d JOIN productos p ON p.id_producto = d.id_producto "
            "LEFT JOIN apex_product_catalog m ON m.product_id = d.id_producto "
            "WHERE d.id_recibo = :id ORDER BY p.pro_nombre"
        )
        result["lines"] = [dict(row) for row in db.execute(text(lines_query), {"id": resource_id}).mappings()]
    elif resource in {"sales", "invoices"}:
        lines_query = (
            "SELECT m.slug AS product_id, p.pro_nombre AS product_name, d.pxf_cantidad AS quantity, "
            "d.pxf_precio AS unit_price, d.pxf_subtotal AS subtotal, d.estado_pxfac AS status "
            "FROM proxfac d JOIN productos p ON p.id_producto = d.id_producto "
            "LEFT JOIN apex_product_catalog m ON m.product_id = d.id_producto "
            "WHERE d.id_factura = :id ORDER BY p.pro_nombre"
        )
        result["lines"] = [dict(row) for row in db.execute(text(lines_query), {"id": resource_id}).mappings()]
    elif resource == "deliveries":
        lines_query = (
            "SELECT m.slug AS product_id, p.pro_nombre AS product_name, d.pxe_cantidad AS quantity, "
            "d.pxe_qty_entregada AS delivered_quantity, d.pxe_motivo AS reason, d.estado_pxent AS status "
            "FROM proxent d JOIN productos p ON p.id_producto = d.id_producto "
            "LEFT JOIN apex_product_catalog m ON m.product_id = d.id_producto "
            "WHERE d.id_entrega = :id ORDER BY p.pro_nombre"
        )
        result["lines"] = [dict(row) for row in db.execute(text(lines_query), {"id": resource_id}).mappings()]
    elif resource == "audit":
        result["old_value"] = item["old_value"]
        result["new_value"] = item["new_value"]
    return result


@router.post("/products", status_code=status.HTTP_201_CREATED)
def create_product(
    body: ProductCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "products", "write")
    if not body.image_url.startswith(("https://", "images/")):
        raise HTTPException(status_code=422, detail="image_url must be a relative image path or HTTPS URL")
    product_id = body.product_id or _new_id(10)
    images = body.images or [ProductImage(url=body.image_url, order=1)]
    try:
        with db.begin():
            db.execute(
                text(
                    "INSERT INTO productos "
                    "(id_producto, id_percha, id_categoria, id_marca, pro_nombre, "
                    "pro_descripcioncorta, pro_material, pro_talla, pro_color, pro_garantia, "
                    "pro_um_compra, pro_um_venta, pro_valor_compra, pro_precio_venta, "
                    "pro_saldo_inicial, pro_qty_ingresos, pro_qty_egresos, pro_qty_ajustes, "
                    "pro_saldo_final, estado_prod) "
                    "VALUES (:id, :shelf, :category, :brand, :name, :description, :material, "
                    ":size, :color, :warranty, :purchase_unit, :sale_unit, :cost, :price, "
                    ":stock, 0, 0, 0, :stock, 'ACT')"
                ),
                {
                    "id": product_id,
                    "shelf": body.shelf_id,
                    "category": body.category_id,
                    "brand": body.brand_id,
                    "name": body.name,
                    "description": body.description,
                    "material": body.material,
                    "size": body.size,
                    "color": body.color,
                    "warranty": body.warranty,
                    "purchase_unit": body.purchase_unit_id,
                    "sale_unit": body.sale_unit_id,
                    "cost": body.cost,
                    "price": body.price,
                    "stock": body.opening_stock,
                },
            )
            db.execute(
                text(
                    "INSERT INTO apex_product_catalog (product_id, slug, category_slug, image_url) "
                    "VALUES (:id, :slug, :category_slug, :image_url)"
                ),
                {
                    "id": product_id,
                    "slug": body.slug,
                    "category_slug": body.category_slug,
                    "image_url": body.image_url,
                },
            )
            for image in images:
                if not image.url.startswith(("https://", "images/")):
                    raise HTTPException(status_code=422, detail="Photo URLs must be relative image paths or HTTPS URLs")
                db.execute(
                    text(
                        "INSERT INTO producto_fotografias "
                        "(id_producto, fot_url, fot_orden, estado_fot) "
                        "VALUES (:id, :url, :ordering, 'ACT')"
                    ),
                    {"id": product_id, "url": image.url, "ordering": image.order},
                )
            _audit(db, principal, "PRODUCTOS", "CREATE", {"id": product_id, "slug": body.slug})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Product data conflicts with catalog or reference data") from exc
    return {"id": body.slug, "productId": product_id, "status": "ACT"}


@router.patch("/products/{slug}/catalog")
def update_product_catalog(
    slug: str,
    body: ProductCatalogUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "products", "write")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No product changes supplied")
    nullable_fields = {"description", "material", "size", "color", "warranty"}
    if any(value is None and key not in nullable_fields for key, value in changes.items()):
        raise HTTPException(status_code=422, detail="Product fields cannot be null")
    product_id = db.execute(
        text("SELECT product_id FROM apex_product_catalog WHERE slug = :slug"),
        {"slug": slug},
    ).scalar_one_or_none()
    if product_id is None:
        raise HTTPException(status_code=404, detail="Product not found")
    db.commit()
    columns = {
        "shelf_id": "id_percha",
        "category_id": "id_categoria",
        "brand_id": "id_marca",
        "purchase_unit_id": "pro_um_compra",
        "sale_unit_id": "pro_um_venta",
        "name": "pro_nombre",
        "description": "pro_descripcioncorta",
        "material": "pro_material",
        "size": "pro_talla",
        "color": "pro_color",
        "warranty": "pro_garantia",
        "cost": "pro_valor_compra",
        "price": "pro_precio_venta",
    }
    product_changes = {key: value for key, value in changes.items() if key in columns}
    try:
        with db.begin():
            if product_changes:
                assignments = [f"{columns[key]} = :{key}" for key in product_changes]
                db.execute(
                    text(f"UPDATE productos SET {', '.join(assignments)} WHERE id_producto = :id"),
                    {"id": product_id, **product_changes},
                )
            mapping_changes = {}
            if "category_slug" in changes:
                mapping_changes["category_slug"] = changes["category_slug"]
            if "image_url" in changes:
                if not changes["image_url"].startswith(("https://", "images/")):
                    raise HTTPException(status_code=422, detail="image_url must be a relative image path or HTTPS URL")
                mapping_changes["image_url"] = changes["image_url"]
            if mapping_changes:
                assignments = [f"{key} = :{key}" for key in mapping_changes]
                db.execute(
                    text(f"UPDATE apex_product_catalog SET {', '.join(assignments)} WHERE product_id = :id"),
                    {"id": product_id, **mapping_changes},
                )
            if "images" in changes:
                db.execute(
                    text("UPDATE producto_fotografias SET estado_fot = 'INA' WHERE id_producto = :id AND estado_fot = 'ACT'"),
                    {"id": product_id},
                )
                for image in changes["images"]:
                    if not image.url.startswith(("https://", "images/")):
                        raise HTTPException(status_code=422, detail="Photo URLs must be relative image paths or HTTPS URLs")
                    db.execute(
                        text(
                            "INSERT INTO producto_fotografias "
                            "(id_producto, fot_url, fot_orden, estado_fot) "
                            "VALUES (:id, :url, :ordering, 'ACT')"
                        ),
                        {"id": product_id, "url": image.url, "ordering": image.order},
                    )
            if "active" in changes:
                db.execute(
                    text("UPDATE productos SET estado_prod = :status WHERE id_producto = :id"),
                    {"id": product_id, "status": "ACT" if changes["active"] else "INA"},
                )
            _audit(db, principal, "PRODUCTOS", "UPDATE", {"slug": slug, **changes})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Product update conflicts with catalog or reference data") from exc
    return {"id": slug, "updated": changes}


@router.post("/suppliers", status_code=status.HTTP_201_CREATED)
def create_supplier(
    body: SupplierCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "suppliers", "write")
    supplier_id = _new_id(10)
    try:
        with db.begin():
            db.execute(
                text(
                    "INSERT INTO proveedores "
                    "(id_proveedor, prv_nombre, prv_ruc_ced, prv_telefono, prv_mail, "
                    "id_ciudad, prv_celular, prv_direccion, estado_prv) "
                    "VALUES (:id, :name, :identification, :phone, :email, :city, :mobile, :address, 'ACT')"
                ),
                {
                    "id": supplier_id,
                    "name": body.name,
                    "identification": body.identification,
                    "phone": body.phone,
                    "email": body.email,
                    "city": body.city_id,
                    "mobile": body.mobile,
                    "address": body.address,
                },
            )
            _audit(db, principal, "PROVEEDORES", "CREATE", {"id": supplier_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Supplier identification or reference data already exists") from exc
    return {"id": supplier_id, "status": "ACT"}


@router.patch("/suppliers/{supplier_id}")
def update_supplier(
    supplier_id: str,
    body: SupplierUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "suppliers", "write")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No supplier changes supplied")
    nullable_fields = {"phone", "mobile", "email", "address"}
    if any(value is None and key not in nullable_fields for key, value in changes.items()):
        raise HTTPException(status_code=422, detail="Supplier fields cannot be null; omit unchanged fields")
    fields = {
        "name": "prv_nombre",
        "identification": "prv_ruc_ced",
        "phone": "prv_telefono",
        "email": "prv_mail",
        "city_id": "id_ciudad",
        "mobile": "prv_celular",
        "address": "prv_direccion",
    }
    assignments = []
    params = {"id": supplier_id}
    for key, value in changes.items():
        if key == "active":
            assignments.append("estado_prv = :status")
            params["status"] = "ACT" if value else "INA"
        else:
            assignments.append(f"{fields[key]} = :{key}")
            params[key] = value
    try:
        with db.begin():
            result = db.execute(
                text(f"UPDATE proveedores SET {', '.join(assignments)} WHERE id_proveedor = :id"),
                params,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Supplier not found")
            _audit(db, principal, "PROVEEDORES", "UPDATE", {"id": supplier_id, **changes})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Supplier update conflicts with unique or reference data") from exc
    return {"id": supplier_id, "updated": changes}


@router.post("/customers", status_code=status.HTTP_201_CREATED)
def create_customer(
    body: CustomerCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "customers", "write")
    customer_id = _new_id(10)
    try:
        with db.begin():
            db.execute(
                text(
                    "INSERT INTO clientes "
                    "(id_cliente, id_usuario, cli_nombre, cli_ruc_ced, cli_telefono, cli_mail, "
                    "id_ciudad, cli_celular, cli_direccion, cli_montomaxcredito, cli_descuento, estado_cli) "
                    "VALUES (:id, :user_id, :name, :identification, :phone, :email, :city_id, "
                    ":mobile, :address, :credit_limit, :discount, 'ACT')"
                ),
                {"id": customer_id, **body.model_dump()},
            )
            _audit(db, principal, "CLIENTES", "CREATE", {"id": customer_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Customer identification, account, or reference data already exists") from exc
    return {"id": customer_id, "status": "ACT"}


@router.patch("/customers/{customer_id}")
def update_customer(
    customer_id: str,
    body: CustomerUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "customers", "write")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No customer changes supplied")
    nullable_fields = {
        "phone", "mobile", "email", "address", "credit_limit", "discount", "user_id"
    }
    if any(value is None and key not in nullable_fields for key, value in changes.items()):
        raise HTTPException(status_code=422, detail="Customer fields cannot be null; omit unchanged fields")
    fields = {
        "name": "cli_nombre",
        "identification": "cli_ruc_ced",
        "phone": "cli_telefono",
        "mobile": "cli_celular",
        "email": "cli_mail",
        "city_id": "id_ciudad",
        "address": "cli_direccion",
        "credit_limit": "cli_montomaxcredito",
        "discount": "cli_descuento",
        "user_id": "id_usuario",
    }
    assignments = []
    params = {"id": customer_id}
    for key, value in changes.items():
        if key == "active":
            assignments.append("estado_cli = :status")
            params["status"] = "ACT" if value else "INA"
        else:
            assignments.append(f"{fields[key]} = :{key}")
            params[key] = value
    try:
        with db.begin():
            result = db.execute(
                text(f"UPDATE clientes SET {', '.join(assignments)} WHERE id_cliente = :id"),
                params,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Customer not found")
            _audit(db, principal, "CLIENTES", "UPDATE", {"id": customer_id, **changes})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Customer update conflicts with unique or reference data") from exc
    return {"id": customer_id, "updated": changes}


@router.post("/purchases", status_code=status.HTTP_201_CREATED)
def create_purchase(
    body: PurchaseCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "purchases", "write")
    purchase_id = _new_id(10)
    subtotal = Decimal("0.00")
    prepared = []
    try:
        with db.begin():
            supplier = db.execute(
                text("SELECT 1 FROM proveedores WHERE id_proveedor = :id AND estado_prv = 'ACT' FOR UPDATE"),
                {"id": body.supplier_id},
            ).scalar_one_or_none()
            if supplier is None:
                raise HTTPException(status_code=404, detail="Active supplier not found")
            for line in body.items:
                product = _slug_product(db, line.product_slug)
                line_subtotal = _convert_amount(line.unit_price * line.quantity)
                subtotal += line_subtotal
                prepared.append((product["product_id"], line, line_subtotal))
            subtotal = _convert_amount(subtotal)
            tax = _convert_amount(subtotal * Decimal("0.15"))
            total = subtotal + tax
            db.execute(
                text(
                    "INSERT INTO compras "
                    "(id_compra, id_proveedor, oc_fecha, oc_fechaentrega, oc_subtotal, oc_iva, oc_total, estado_oc) "
                    "VALUES (:id, :supplier, :created, :delivery, 0, 0, 0, 'ABI')"
                ),
                {
                    "id": purchase_id,
                    "supplier": body.supplier_id,
                    "created": body.created_at,
                    "delivery": body.delivery_at,
                },
            )
            for product_id, line, line_subtotal in prepared:
                db.execute(
                    text(
                        "INSERT INTO proxoc "
                        "(id_compra, id_producto, pxo_cantidad, pxo_valor, pxo_subtotal, estado_pxoc) "
                        "VALUES (:purchase, :product, :quantity, :price, :subtotal, 'ACT')"
                    ),
                    {
                        "purchase": purchase_id,
                        "product": product_id,
                        "quantity": line.quantity,
                        "price": line.unit_price,
                        "subtotal": line_subtotal,
                    },
                )
            db.execute(
                text(
                    "UPDATE compras SET oc_subtotal = :subtotal, oc_iva = :tax, oc_total = :total "
                    "WHERE id_compra = :id"
                ),
                {"id": purchase_id, "subtotal": subtotal, "tax": tax, "total": total},
            )
            _audit(db, principal, "COMPRAS", "CREATE", {"id": purchase_id, "subtotal": str(subtotal)})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Purchase conflicts with database references") from exc
    return {"id": purchase_id, "status": "ABI", "subtotal": subtotal, "tax": tax, "total": total}


@router.post("/purchases/{purchase_id}/approve")
def approve_purchase(
    purchase_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "purchases", "write")
    with db.begin():
        result = db.execute(
            text(
                "UPDATE compras SET estado_oc = 'APR' "
                "WHERE id_compra = :id AND estado_oc = 'ABI'"
            ),
            {"id": purchase_id},
        )
        if result.rowcount == 0:
            exists = db.execute(
                text("SELECT 1 FROM compras WHERE id_compra = :id"),
                {"id": purchase_id},
            ).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="Purchase not found")
            raise HTTPException(status_code=409, detail="Purchase is not awaiting approval")
        receipt_id = db.execute(
            text("SELECT id_recibo FROM recepciones WHERE id_compra = :id ORDER BY rec_fechahora DESC LIMIT 1"),
            {"id": purchase_id},
        ).scalar_one_or_none()
        _audit(db, principal, "COMPRAS", "APPROVE", {"id": purchase_id})
    return {"id": purchase_id, "status": "APR", "receiptId": receipt_id.strip() if receipt_id else None}


@router.post("/receipts/{receipt_id}/lines")
def add_receipt_lines(
    receipt_id: str,
    body: ReceiptLinesRequest,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "receipts", "write")
    try:
        with db.begin():
            receipt = db.execute(
                text(
                    "SELECT r.id_compra, r.estado_rec FROM recepciones r "
                    "WHERE r.id_recibo = :id FOR UPDATE"
                ),
                {"id": receipt_id},
            ).mappings().one_or_none()
            if receipt is None:
                raise HTTPException(status_code=404, detail="Receipt not found")
            if receipt["estado_rec"] != "PEN":
                raise HTTPException(status_code=409, detail="Receipt is already confirmed")
            purchase_status = db.execute(
                text("SELECT estado_oc FROM compras WHERE id_compra = :id FOR UPDATE"),
                {"id": receipt["id_compra"]},
            ).scalar_one()
            if purchase_status != "APR":
                raise HTTPException(status_code=409, detail="Only approved purchases can receive products")
            existing_lines = db.execute(
                text("SELECT count(*) FROM proxrec WHERE id_recibo = :id"),
                {"id": receipt_id},
            ).scalar_one()
            if existing_lines:
                raise HTTPException(status_code=409, detail="Receipt lines have already been recorded")
            for line in body.items:
                product = _slug_product(db, line.product_slug)
                ordered_quantity = db.execute(
                    text(
                        "SELECT pxo_cantidad FROM proxoc "
                        "WHERE id_compra = :purchase AND id_producto = :product"
                    ),
                    {"purchase": receipt["id_compra"], "product": product["product_id"]},
                ).scalar_one_or_none()
                if ordered_quantity is None:
                    raise HTTPException(status_code=422, detail="Receipt product is not part of its purchase")
                received_before = db.execute(
                    text(
                        "SELECT COALESCE(SUM(pr.pxr_qty_recibida), 0) "
                        "FROM proxrec pr JOIN recepciones r ON r.id_recibo = pr.id_recibo "
                        "WHERE r.id_compra = :purchase AND pr.id_producto = :product "
                        "AND r.estado_rec IN ('PEN', 'REC')"
                    ),
                    {"purchase": receipt["id_compra"], "product": product["product_id"]},
                ).scalar_one()
                if received_before + line.received_quantity > ordered_quantity:
                    raise HTTPException(status_code=409, detail="Received quantity exceeds the outstanding purchase quantity")
                db.execute(
                    text(
                        "INSERT INTO proxrec "
                        "(id_recibo, id_producto, pxr_cantidad, pxr_qty_recibida, pxr_motivo, estado_pxrec) "
                        "VALUES (:receipt, :product, :expected, :received, :reason, 'ACT')"
                    ),
                    {
                        "receipt": receipt_id,
                        "product": product["product_id"],
                        "expected": ordered_quantity,
                        "received": line.received_quantity,
                        "reason": line.reason,
                    },
                )
            db.execute(
                text("UPDATE recepciones SET rec_num_produc = :count WHERE id_recibo = :id"),
                {"count": len(body.items), "id": receipt_id},
            )
            _audit(db, principal, "RECEPCIONES", "ADD_LINES", {"id": receipt_id, "line_count": len(body.items)})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Receipt lines conflict with purchase data") from exc
    return {"id": receipt_id, "lineCount": len(body.items), "status": "PEN"}


@router.post("/purchases/{purchase_id}/receipts", status_code=status.HTTP_201_CREATED)
def create_purchase_receipt(
    purchase_id: str,
    body: ReceiptCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "receipts", "write")
    receipt_id = _new_id(10)
    try:
        with db.begin():
            purchase_status = db.execute(
                text("SELECT estado_oc FROM compras WHERE id_compra = :id FOR UPDATE"),
                {"id": purchase_id},
            ).scalar_one_or_none()
            if purchase_status is None:
                raise HTTPException(status_code=404, detail="Purchase not found")
            if purchase_status != "APR":
                raise HTTPException(status_code=409, detail="Only approved purchases can receive products")
            db.execute(
                text(
                    "INSERT INTO recepciones "
                    "(id_recibo, id_compra, rec_descripcion, rec_fechahora, rec_num_produc, estado_rec) "
                    "VALUES (:id, :purchase, :description, :created, 0, 'PEN')"
                ),
                {
                    "id": receipt_id,
                    "purchase": purchase_id,
                    "description": body.description,
                    "created": body.created_at,
                },
            )
            _audit(db, principal, "RECEPCIONES", "CREATE", {"id": receipt_id, "purchase_id": purchase_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Receipt conflicts with purchase data") from exc
    return {"id": receipt_id, "purchaseId": purchase_id, "status": "PEN"}


@router.post("/receipts/{receipt_id}/confirm")
def confirm_receipt(
    receipt_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "receipts", "write")
    try:
        with db.begin():
            result = db.execute(
                text(
                    "UPDATE recepciones SET estado_rec = 'REC' "
                    "WHERE id_recibo = :id AND estado_rec = 'PEN' "
                    "AND EXISTS (SELECT 1 FROM proxrec WHERE id_recibo = :id)"
                ),
                {"id": receipt_id},
            )
            if result.rowcount == 0:
                exists = db.execute(
                    text("SELECT estado_rec FROM recepciones WHERE id_recibo = :id"),
                    {"id": receipt_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise HTTPException(status_code=404, detail="Receipt not found")
                raise HTTPException(status_code=409, detail="Receipt needs pending status and recorded lines")
            movements = db.execute(
                text(
                    "SELECT id_producto, pxr_qty_recibida FROM proxrec "
                    "WHERE id_recibo = :id AND pxr_qty_recibida > 0"
                ),
                {"id": receipt_id},
            ).mappings()
            for movement in movements:
                db.execute(
                    text(
                        "INSERT INTO apex_inventory_movements "
                        "(product_id, actor_id, quantity_delta, reason, reference_id) "
                        "VALUES (:product, :actor, :quantity, 'purchase_receipt', :reference)"
                    ),
                    {
                        "product": movement["id_producto"],
                        "actor": principal.id,
                        "quantity": movement["pxr_qty_recibida"],
                        "reference": receipt_id,
                    },
                )
            _audit(db, principal, "RECEPCIONES", "CONFIRM", {"id": receipt_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Receipt confirmation conflicts with existing data") from exc
    return {"id": receipt_id, "status": "REC"}


@router.post("/inventory/adjustments", status_code=status.HTTP_201_CREATED)
def create_inventory_adjustment(
    body: AdjustmentCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "inventory", "write")
    adjustment_id = _new_id(15)
    try:
        with db.begin():
            prepared = []
            for line in body.items:
                product = _slug_product(db, line.product_slug)
                new_stock = int(product["stock"] or 0) + line.delta
                if new_stock < 0:
                    raise HTTPException(status_code=409, detail="Adjustment would make stock negative")
                prepared.append((product["product_id"], line.delta, new_stock))
            db.execute(
                text(
                    "INSERT INTO ajustes "
                    "(id_ajuste, id_usuario, aju_descripcion, aju_fechahora, aju_num_produc, estado_aju) "
                    "VALUES (:id, :user, :description, :created, :count, 'ABI')"
                ),
                {
                    "id": adjustment_id,
                    "user": principal.id,
                    "description": body.description,
                    "created": body.created_at,
                    "count": len(body.items),
                },
            )
            for product_id, delta, new_stock in prepared:
                db.execute(
                    text(
                        "INSERT INTO proxaju "
                        "(id_ajuste, id_producto, pxa_cantidad, pxa_qty_ajustada, estado_pxaju) "
                        "VALUES (:adjustment, :product, :delta, :new_stock, 'ACT')"
                    ),
                    {
                        "adjustment": adjustment_id,
                        "product": product_id,
                        "delta": delta,
                        "new_stock": new_stock,
                    },
                )
            _audit(db, principal, "AJUSTES", "CREATE", {"id": adjustment_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Adjustment conflicts with product data") from exc
    return {"id": adjustment_id, "status": "ABI"}


@router.post("/inventory/adjustments/{adjustment_id}/approve")
def approve_inventory_adjustment(
    adjustment_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "inventory", "write")
    try:
        with db.begin():
            result = db.execute(
                text(
                    "UPDATE ajustes SET estado_aju = 'APR' "
                    "WHERE id_ajuste = :id AND estado_aju = 'ABI' "
                    "AND EXISTS (SELECT 1 FROM proxaju WHERE id_ajuste = :id)"
                ),
                {"id": adjustment_id},
            )
            if result.rowcount == 0:
                exists = db.execute(
                    text("SELECT 1 FROM ajustes WHERE id_ajuste = :id"),
                    {"id": adjustment_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise HTTPException(status_code=404, detail="Inventory adjustment not found")
                raise HTTPException(status_code=409, detail="Adjustment is not pending or has no lines")
            movements = db.execute(
                text("SELECT id_producto, pxa_cantidad FROM proxaju WHERE id_ajuste = :id"),
                {"id": adjustment_id},
            ).mappings()
            for movement in movements:
                db.execute(
                    text(
                        "INSERT INTO apex_inventory_movements "
                        "(product_id, actor_id, quantity_delta, reason, reference_id) "
                        "VALUES (:product, :actor, :quantity, 'inventory_adjustment', :reference)"
                    ),
                    {
                        "product": movement["id_producto"],
                        "actor": principal.id,
                        "quantity": movement["pxa_cantidad"],
                        "reference": adjustment_id,
                    },
                )
            _audit(db, principal, "AJUSTES", "APPROVE", {"id": adjustment_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Adjustment approval conflicts with inventory constraints") from exc
    except DBAPIError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23514":
            raise HTTPException(status_code=409, detail="Adjustment would make stock negative") from exc
        raise
    return {"id": adjustment_id, "status": "APR"}


def _create_invoice_record(
    body: InvoiceCreate,
    principal: Principal,
    db: Session,
    permission_module: str,
) -> dict:
    _check_permission(db, principal, permission_module, "write")
    invoice_id = _new_id(15)
    subtotal = Decimal("0.00")
    prepared = []
    try:
        with db.begin():
            customer = db.execute(
                text("SELECT 1 FROM clientes WHERE id_cliente = :id AND estado_cli = 'ACT' FOR UPDATE"),
                {"id": body.customer_id},
            ).scalar_one_or_none()
            if customer is None:
                raise HTTPException(status_code=404, detail="Active customer not found")
            for line in body.items:
                product = _slug_product(db, line.product_slug)
                unit_price = Decimal(product["price"])
                line_subtotal = _convert_amount(unit_price * line.quantity)
                subtotal += line_subtotal
                prepared.append((product["product_id"], line.quantity, unit_price, line_subtotal))
            subtotal = _convert_amount(subtotal)
            if body.discount > subtotal:
                raise HTTPException(status_code=422, detail="Discount cannot exceed invoice subtotal")
            taxable = subtotal - body.discount
            tax = _convert_amount(taxable * Decimal("0.15"))
            total = taxable + tax
            db.execute(
                text(
                    "INSERT INTO facturas "
                    "(id_factura, id_cliente, id_usuario, fac_descripcion, fac_fecha, fac_descuento, "
                    "fac_subtotal, fac_iva, fac_formapago, fac_total, estado_fac) "
                    "VALUES (:id, :customer, :user, :description, :created, :discount, "
                    ":subtotal, :tax, :payment, :total, 'ABI')"
                ),
                {
                    "id": invoice_id,
                    "customer": body.customer_id,
                    "user": principal.id,
                    "description": body.description,
                    "created": body.created_at,
                    "discount": body.discount,
                    "subtotal": Decimal("0.00"),
                    "tax": Decimal("0.00"),
                    "payment": body.payment_method,
                    "total": Decimal("0.00"),
                },
            )
            for product_id, quantity, unit_price, line_subtotal in prepared:
                db.execute(
                    text(
                        "INSERT INTO proxfac "
                        "(id_factura, id_producto, pxf_cantidad, pxf_precio, pxf_subtotal, estado_pxfac) "
                        "VALUES (:invoice, :product, :quantity, :price, :subtotal, 'ACT')"
                    ),
                    {
                        "invoice": invoice_id,
                        "product": product_id,
                        "quantity": quantity,
                        "price": unit_price,
                        "subtotal": line_subtotal,
                    },
                )
            db.execute(
                text(
                    "UPDATE facturas SET fac_subtotal = :subtotal, fac_iva = :tax, fac_total = :total "
                    "WHERE id_factura = :id"
                ),
                {"id": invoice_id, "subtotal": subtotal, "tax": tax, "total": total},
            )
            _audit(db, principal, "FACTURAS", "CREATE", {"id": invoice_id, "status": "ABI"})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Invoice conflicts with customer or product data") from exc
    return {
        "id": invoice_id,
        "status": "ABI",
        "subtotal": subtotal,
        "discount": body.discount,
        "tax": tax,
        "total": total,
        "paymentConfirmed": False,
    }


@router.post("/invoices", status_code=status.HTTP_201_CREATED)
def create_invoice(
    body: InvoiceCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _create_invoice_record(body, principal, db, "invoices")


@router.post("/sales", status_code=status.HTTP_201_CREATED)
def create_sale(
    body: InvoiceCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _create_invoice_record(body, principal, db, "sales")


@router.post("/invoices/{invoice_id}/approve")
def approve_invoice(
    invoice_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _approve_invoice_record(invoice_id, principal, db, "invoices")


def _approve_invoice_record(
    invoice_id: str,
    principal: Principal,
    db: Session,
    permission_module: str,
) -> dict:
    _check_permission(db, principal, permission_module, "write")
    try:
        with db.begin():
            result = db.execute(
                text(
                    "UPDATE facturas SET estado_fac = 'APR' "
                    "WHERE id_factura = :id AND estado_fac = 'ABI' "
                    "AND EXISTS (SELECT 1 FROM proxfac WHERE id_factura = :id)"
                ),
                {"id": invoice_id},
            )
            if result.rowcount == 0:
                exists = db.execute(
                    text("SELECT estado_fac FROM facturas WHERE id_factura = :id"),
                    {"id": invoice_id},
                ).scalar_one_or_none()
                if exists is None:
                    raise HTTPException(status_code=404, detail="Invoice not found")
                raise HTTPException(status_code=409, detail="Invoice is not pending or has no lines")
            lines = db.execute(
                text("SELECT id_producto, pxf_cantidad FROM proxfac WHERE id_factura = :id"),
                {"id": invoice_id},
            ).mappings()
            for line in lines:
                db.execute(
                    text(
                        "INSERT INTO apex_inventory_movements "
                        "(product_id, actor_id, quantity_delta, reason, reference_id) "
                        "VALUES (:product, :actor, :quantity, 'admin_sale', :reference)"
                    ),
                    {
                        "product": line["id_producto"],
                        "actor": principal.id,
                        "quantity": -line["pxf_cantidad"],
                        "reference": invoice_id,
                    },
                )
            delivery = db.execute(
                text("SELECT id_entrega FROM entregas WHERE id_factura = :id"),
                {"id": invoice_id},
            ).scalar_one_or_none()
            _audit(db, principal, "FACTURAS", "APPROVE", {"id": invoice_id})
    except DBAPIError as exc:
        db.rollback()
        if getattr(exc.orig, "sqlstate", None) == "23514":
            raise HTTPException(status_code=409, detail="Insufficient inventory to approve invoice") from exc
        raise
    return {
        "id": invoice_id,
        "status": "APR",
        "deliveryId": delivery.strip() if delivery else None,
        "paymentConfirmed": False,
    }


@router.post("/sales/{invoice_id}/approve")
def approve_sale(
    invoice_id: str,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    return _approve_invoice_record(invoice_id, principal, db, "sales")


@router.post("/deliveries/{delivery_id}/complete")
def complete_delivery(
    delivery_id: str,
    body: DeliveryCompletionRequest,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "deliveries", "write")
    try:
        with db.begin():
            delivery = db.execute(
                text("SELECT estado_ent FROM entregas WHERE id_entrega = :id FOR UPDATE"),
                {"id": delivery_id},
            ).scalar_one_or_none()
            if delivery is None:
                raise HTTPException(status_code=404, detail="Delivery not found")
            if delivery != "PEN":
                raise HTTPException(status_code=409, detail="Delivery is not pending")
            lines = db.execute(
                text(
                    "SELECT d.id_producto, d.pxe_cantidad, m.slug "
                    "FROM proxent d JOIN apex_product_catalog m ON m.product_id = d.id_producto "
                    "WHERE d.id_entrega = :id ORDER BY d.id_producto FOR UPDATE OF d"
                ),
                {"id": delivery_id},
            ).mappings().all()
            expected = {line["slug"]: (line["id_producto"], line["pxe_cantidad"]) for line in lines}
            supplied = {line.product_slug: line for line in body.items}
            if set(expected) != set(supplied):
                raise HTTPException(status_code=422, detail="Delivery must include every invoiced product exactly once")
            for slug, (product_id, quantity) in expected.items():
                line = supplied[slug]
                if line.delivered_quantity != quantity:
                    raise HTTPException(
                        status_code=422,
                        detail="Partial delivery is not enabled until its status transitions are defined",
                    )
                db.execute(
                    text(
                        "UPDATE proxent SET pxe_qty_entregada = :delivered, pxe_motivo = :reason "
                        "WHERE id_entrega = :delivery AND id_producto = :product"
                    ),
                    {
                        "delivered": line.delivered_quantity,
                        "reason": line.reason,
                        "delivery": delivery_id,
                        "product": product_id,
                    },
                )
            result = db.execute(
                text("UPDATE entregas SET estado_ent = 'ENT' WHERE id_entrega = :id AND estado_ent = 'PEN'"),
                {"id": delivery_id},
            )
            if result.rowcount != 1:
                raise HTTPException(status_code=409, detail="Delivery state changed; reload and retry")
            _audit(db, principal, "ENTREGAS", "COMPLETE", {"id": delivery_id})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Delivery completion conflicts with invoice details") from exc
    return {"id": delivery_id, "status": "ENT"}


@router.patch("/users/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "users", "write")
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No user changes supplied")
    if any(value is None and key != "phone" for key, value in changes.items()):
        raise HTTPException(status_code=422, detail="User fields cannot be null; omit unchanged fields")
    if user_id == principal.id and changes.get("active") is False:
        raise HTTPException(status_code=409, detail="You cannot deactivate your own account")
    if changes.get("active") is False:
        is_superadmin = db.execute(
            text(
                "SELECT 1 FROM usuariosxroles ur JOIN roles r ON r.id_rol = ur.id_rol "
                "WHERE ur.id_usuario = :id AND ur.estado_uxr = 'ACT' "
                "AND r.estado_rol = 'ACT' AND lower(trim(r.rol_nombre)) = 'superadministrador' "
                "LIMIT 1"
            ),
            {"id": user_id},
        ).scalar_one_or_none()
        db.commit()
        if is_superadmin:
            remaining = db.execute(
                text(
                    "SELECT COUNT(DISTINCT u.id_usuario) FROM usuarios u "
                    "JOIN usuariosxroles ur ON ur.id_usuario = u.id_usuario "
                    "JOIN roles r ON r.id_rol = ur.id_rol "
                    "WHERE u.estado_usr = 'ACT' AND ur.estado_uxr = 'ACT' "
                    "AND r.estado_rol = 'ACT' "
                    "AND lower(trim(r.rol_nombre)) = 'superadministrador' "
                    "AND u.id_usuario <> :id"
                ),
                {"id": user_id},
            ).scalar_one()
            db.commit()
            if remaining == 0:
                raise HTTPException(status_code=409, detail="Cannot deactivate the last active superadministrator")
    fields = {
        "username": "usr_username",
        "first_name": "usr_nombre",
        "last_name": "usr_apellido",
        "email": "usr_mail",
        "phone": "usr_telefono",
    }
    assignments = []
    params = {"id": user_id}
    for key, value in changes.items():
        if key == "active":
            assignments.append("estado_usr = :status")
            params["status"] = "ACT" if value else "INA"
        else:
            assignments.append(f"{fields[key]} = :{key}")
            params[key] = value
    try:
        with db.begin():
            result = db.execute(
                text(f"UPDATE usuarios SET {', '.join(assignments)} WHERE id_usuario = :id"),
                params,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="User not found")
            _audit(db, principal, "USUARIOS", "UPDATE", {"id": user_id, **changes})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "User email or username is already in use") from exc
    return {"id": user_id, "updated": changes}


def _require_superadmin(principal: Principal) -> None:
    if "superadministrador" not in principal.roles:
        raise HTTPException(status_code=403, detail="Superadministrator role required")


@router.put("/users/{user_id}/roles")
def assign_user_roles(
    user_id: str,
    body: RoleAssignment,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "users", "write")
    _require_superadmin(principal)
    if user_id == principal.id:
        raise HTTPException(status_code=409, detail="You cannot replace your own role assignments")
    if len(set(body.role_ids)) != len(body.role_ids):
        raise HTTPException(status_code=422, detail="Role IDs must be unique")
    try:
        with db.begin():
            current_roles = db.execute(
                text(
                    "SELECT lower(trim(r.rol_nombre)) FROM usuariosxroles ur "
                    "JOIN roles r ON r.id_rol = ur.id_rol "
                    "WHERE ur.id_usuario = :id AND ur.estado_uxr = 'ACT' "
                    "AND r.estado_rol = 'ACT' FOR UPDATE OF ur"
                ),
                {"id": user_id},
            ).scalars().all()
            exists = db.execute(
                text("SELECT 1 FROM usuarios WHERE id_usuario = :id FOR UPDATE"),
                {"id": user_id},
            ).scalar_one_or_none()
            if exists is None:
                raise HTTPException(status_code=404, detail="User not found")
            requested_role_names = []
            for role_id in body.role_ids:
                role_name = db.execute(
                    text("SELECT lower(trim(rol_nombre)) FROM roles WHERE id_rol = :id"),
                    {"id": role_id},
                ).scalar_one_or_none()
                if role_name is None:
                    raise HTTPException(status_code=422, detail=f"Active role not found: {role_id}")
                requested_role_names.append(role_name)
            loses_superadmin = (
                "superadministrador" in current_roles
                and "superadministrador" not in requested_role_names
            )
            if loses_superadmin:
                remaining = db.execute(
                    text(
                        "SELECT COUNT(DISTINCT u.id_usuario) FROM usuarios u "
                        "JOIN usuariosxroles ur ON ur.id_usuario = u.id_usuario "
                        "JOIN roles r ON r.id_rol = ur.id_rol "
                        "WHERE u.estado_usr = 'ACT' AND ur.estado_uxr = 'ACT' "
                        "AND r.estado_rol = 'ACT' "
                        "AND lower(trim(r.rol_nombre)) = 'superadministrador' "
                        "AND u.id_usuario <> :id"
                    ),
                    {"id": user_id},
                ).scalar_one()
                if remaining == 0:
                    raise HTTPException(status_code=409, detail="Cannot remove the last active superadministrator")
            db.execute(text("DELETE FROM usuariosxroles WHERE id_usuario = :id"), {"id": user_id})
            for role_id in body.role_ids:
                db.execute(
                    text(
                        "INSERT INTO usuariosxroles (id_usuario, id_rol, estado_uxr) "
                        "SELECT :user, id_rol, 'ACT' FROM roles "
                        "WHERE id_rol = :role AND estado_rol = 'ACT'"
                    ),
                    {"user": user_id, "role": role_id},
                )
                assigned = db.execute(
                    text(
                        "SELECT 1 FROM usuariosxroles WHERE id_usuario = :user "
                        "AND id_rol = :role"
                    ),
                    {"user": user_id, "role": role_id},
                ).scalar_one_or_none()
                if assigned is None:
                    raise HTTPException(status_code=422, detail=f"Active role not found: {role_id}")
            _audit(db, principal, "USUARIOSxROLES", "REPLACE", {"id": user_id, "role_ids": body.role_ids})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "User role assignment conflicts with account data") from exc
    return {"userId": user_id, "roleIds": body.role_ids}


@router.post("/roles", status_code=status.HTTP_201_CREATED)
def create_role(
    body: RoleCreate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "roles", "write")
    _require_superadmin(principal)
    role_id = _new_id(10)
    try:
        with db.begin():
            db.execute(
                text(
                    "INSERT INTO roles (id_rol, rol_nombre, rol_descripcion, estado_rol) "
                    "VALUES (:id, :name, :description, 'ACT')"
                ),
                {"id": role_id, "name": body.name, "description": body.description},
            )
            _audit(db, principal, "ROLES", "CREATE", {"id": role_id, "name": body.name})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Role conflicts with database constraints") from exc
    return {"id": role_id, "status": "ACT"}


@router.patch("/roles/{role_id}")
def update_role(
    role_id: str,
    body: RoleUpdate,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "roles", "write")
    _require_superadmin(principal)
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=422, detail="No role changes supplied")
    if any(value is None for value in changes.values()):
        raise HTTPException(status_code=422, detail="Role fields cannot be null; omit unchanged fields")
    if changes.get("active") is False:
        role_name = db.execute(
            text("SELECT lower(trim(rol_nombre)) FROM roles WHERE id_rol = :id"),
            {"id": role_id},
        ).scalar_one_or_none()
        db.commit()
        if role_name in {"admin", "administrador", "superadministrador"}:
            raise HTTPException(status_code=409, detail="Built-in administrator roles cannot be deactivated")
    fields = {"name": "rol_nombre", "description": "rol_descripcion"}
    assignments = []
    params = {"id": role_id}
    for key, value in changes.items():
        if key == "active":
            assignments.append("estado_rol = :status")
            params["status"] = "ACT" if value else "INA"
        else:
            assignments.append(f"{fields[key]} = :{key}")
            params[key] = value
    try:
        with db.begin():
            result = db.execute(
                text(f"UPDATE roles SET {', '.join(assignments)} WHERE id_rol = :id"),
                params,
            )
            if result.rowcount == 0:
                raise HTTPException(status_code=404, detail="Role not found")
            _audit(db, principal, "ROLES", "UPDATE", {"id": role_id, **changes})
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Role update conflicts with database constraints") from exc
    return {"id": role_id, "updated": changes}


@router.put("/roles/{role_id}/permissions")
def replace_role_permissions(
    role_id: str,
    body: PermissionAssignment,
    principal: Principal = Depends(require_admin),
    db: Session = Depends(get_db),
) -> dict:
    _check_permission(db, principal, "permissions", "write")
    _require_superadmin(principal)
    if len(set(body.permission_ids)) != len(body.permission_ids):
        raise HTTPException(status_code=422, detail="Permission IDs must be unique")
    try:
        with db.begin():
            role = db.execute(
                text("SELECT 1 FROM roles WHERE id_rol = :id AND estado_rol = 'ACT' FOR UPDATE"),
                {"id": role_id},
            ).scalar_one_or_none()
            if role is None:
                raise HTTPException(status_code=404, detail="Active role not found")
            valid = db.execute(
                text(
                    "SELECT id_permiso FROM permisos WHERE id_permiso = ANY(:ids) "
                    "AND estado_prm = 'ACT'"
                ),
                {"ids": body.permission_ids},
            ).scalars().all() if body.permission_ids else []
            if set(valid) != set(body.permission_ids):
                raise HTTPException(status_code=422, detail="One or more permission IDs are invalid or inactive")
            db.execute(
                text("UPDATE rolesxpermisos SET estado_rxp = 'INA' WHERE id_rol = :id"),
                {"id": role_id},
            )
            for permission_id in body.permission_ids:
                db.execute(
                    text(
                        "INSERT INTO rolesxpermisos (id_rol, id_permiso, estado_rxp) "
                        "VALUES (:role, :permission, 'ACT') "
                        "ON CONFLICT (id_rol, id_permiso) DO UPDATE SET estado_rxp = 'ACT'"
                    ),
                    {"role": role_id, "permission": permission_id},
                )
            _audit(
                db, principal, "ROLESxPERMISOS", "REPLACE",
                {"id": role_id, "permission_ids": body.permission_ids},
            )
    except IntegrityError as exc:
        db.rollback()
        raise _record_integrity_conflict(exc, "Role permission assignment conflicts with database data") from exc
    return {"roleId": role_id, "permissionIds": body.permission_ids}
