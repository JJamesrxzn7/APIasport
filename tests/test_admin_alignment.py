from datetime import datetime
from decimal import Decimal

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.main import app
from app.routers.admin_operations import (
    AdjustmentLine,
    InvoiceCreate,
    ProductCreate,
    UserUpdate,
)
from app.security import Principal, authorize_permission


def test_admin_workflow_routes_are_registered_and_protected() -> None:
    paths = app.openapi()["paths"]
    expected = {
        "/api/v1/admin/lookups/{lookup}": "get",
        "/api/v1/admin/resources/{resource}/{resource_id}": "get",
        "/api/v1/admin/products": "post",
        "/api/v1/admin/products/{slug}/catalog": "patch",
        "/api/v1/admin/suppliers": "post",
        "/api/v1/admin/customers": "post",
        "/api/v1/admin/purchases": "post",
        "/api/v1/admin/purchases/{purchase_id}/approve": "post",
        "/api/v1/admin/purchases/{purchase_id}/receipts": "post",
        "/api/v1/admin/receipts/{receipt_id}/confirm": "post",
        "/api/v1/admin/inventory/adjustments": "post",
        "/api/v1/admin/inventory/adjustments/{adjustment_id}/approve": "post",
        "/api/v1/admin/sales": "post",
        "/api/v1/admin/sales/{invoice_id}/approve": "post",
        "/api/v1/admin/invoices": "post",
        "/api/v1/admin/deliveries/{delivery_id}/complete": "post",
        "/api/v1/admin/users/{user_id}/roles": "put",
        "/api/v1/admin/roles/{role_id}/permissions": "put",
    }

    for path, method in expected.items():
        operation = paths[path][method]
        assert operation["security"]


def test_admin_product_contract_matches_legacy_reference_columns() -> None:
    product = ProductCreate.model_validate(
        {
            "slug": "tennis-racket",
            "category_slug": "tenis",
            "image_url": "images/products/tennis-racket.svg",
            "shelf_id": "SHELF00001",
            "category_id": "CAT0000001",
            "brand_id": "BRAND00001",
            "purchase_unit_id": "UND",
            "sale_unit_id": "UND",
            "name": "Racket",
            "cost": "35.20",
            "price": "52.99",
            "opening_stock": 4,
        }
    )
    assert product.price == Decimal("52.99")
    assert product.opening_stock == 4
    with pytest.raises(ValidationError):
        ProductCreate.model_validate(
            {
                **product.model_dump(),
                "unknown_admin_field": "ignored",
            }
        )


def test_admin_invoice_contract_forbids_unprocessed_payment_and_duplicate_lines() -> None:
    payload = {
        "customer_id": "CUSTOMER01",
        "payment_method": "Efectivo",
        "created_at": datetime(2026, 11, 2, 10, 0),
        "items": [
            {"product_slug": "racket", "quantity": 1},
            {"product_slug": "racket", "quantity": 2},
        ],
    }
    with pytest.raises(ValidationError):
        InvoiceCreate.model_validate(payload)
    with pytest.raises(ValidationError):
        InvoiceCreate.model_validate({**payload, "items": [{"product_slug": "racket", "quantity": 1}], "payment_method": "demo"})


def test_admin_adjustments_require_a_nonzero_signed_delta() -> None:
    assert AdjustmentLine(product_slug="shoe", delta=-3).delta == -3
    with pytest.raises(ValidationError):
        AdjustmentLine(product_slug="shoe", delta=0)


def test_user_update_does_not_accept_credentials_or_role_injection() -> None:
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({"email": "user@example.com", "password": "do-not-accept"})
    with pytest.raises(ValidationError):
        UserUpdate.model_validate({"roles": ["superadministrador"]})


class PermissionSession:
    def __init__(self, permission: bool) -> None:
        self.permission = permission
        self.parameters = None
        self.committed = False

    def execute(self, _statement, parameters):
        self.parameters = parameters
        return self

    def scalar_one_or_none(self):
        return 1 if self.permission else None

    def commit(self):
        self.committed = True


def test_admin_permissions_are_checked_for_each_module_action() -> None:
    principal = Principal("admin-id", "admin@example.com", "Admin User", frozenset({"administrador"}))
    allowed_db = PermissionSession(permission=True)

    authorize_permission(allowed_db, principal, "inventory", "write")

    assert allowed_db.parameters == {
        "user_id": "admin-id",
        "module": "inventory",
        "action": "write",
    }
    assert allowed_db.committed

    with pytest.raises(HTTPException) as error:
        authorize_permission(PermissionSession(permission=False), principal, "invoices", "write")
    assert error.value.status_code == 403
