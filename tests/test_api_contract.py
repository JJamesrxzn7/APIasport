from app.main import app


def test_versioned_storefront_and_admin_routes_are_registered() -> None:
    paths = app.openapi()["paths"]
    assert "/api/v1/auth/register" in paths
    assert "/api/v1/auth/admin/register" in paths
    assert "/api/v1/auth/admin/invitations" in paths
    assert "/api/v1/products" in paths
    assert "/api/v1/cart/items" in paths
    assert "/api/v1/favorites" in paths
    assert "/api/v1/favorites/{product_slug}" in paths
    assert "/api/v1/checkout" in paths
    assert "/api/v1/orders/{order_id}" in paths
    assert "/api/v1/admin/resources/{resource}" in paths


def test_openapi_does_not_expose_payment_card_fields() -> None:
    schemas = app.openapi()["components"]["schemas"]
    assert "CheckoutRequest" in schemas
    checkout_fields = schemas["CheckoutRequest"]["properties"]
    assert "payment_method" in checkout_fields
    assert "card_number" not in checkout_fields
    assert "cvv" not in checkout_fields


def test_admin_invitation_creation_requires_authenticated_admin() -> None:
    paths = app.openapi()["paths"]
    invitation_security = paths["/api/v1/auth/admin/invitations"]["post"].get("security")
    registration_security = paths["/api/v1/auth/admin/register"]["post"].get("security")

    assert invitation_security
    assert registration_security is None
