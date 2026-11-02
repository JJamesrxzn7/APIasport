import pytest
from pydantic import ValidationError

from app.schemas import CartItemRequest, CheckoutRequest, RegisterRequest


def valid_registration() -> dict:
    return {
        "full_name": "Ada Lovelace",
        "email": "Ada@example.com",
        "password": "a-very-long-password",
        "identification": "1234567890",
        "city_id": "UIO",
    }


def test_registration_normalizes_email_and_rejects_role_injection() -> None:
    request = RegisterRequest.model_validate(valid_registration())
    assert request.email == "ada@example.com"
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({**valid_registration(), "role": "admin"})


def test_registration_requires_customer_profile_fields() -> None:
    data = valid_registration()
    data.pop("city_id")
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate(data)


def test_registration_does_not_require_delivery_or_profile_contact_fields() -> None:
    request = RegisterRequest.model_validate(valid_registration())
    assert not hasattr(request, "phone")
    assert not hasattr(request, "address")
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({**valid_registration(), "phone": "0991234567"})


def test_registration_respects_legacy_username_and_customer_name_limits() -> None:
    email_at_limit = "a" * 45 + "@b.co"
    name_at_limit = "A" * 38 + " B"
    request = RegisterRequest.model_validate(
        {**valid_registration(), "email": email_at_limit, "full_name": name_at_limit}
    )
    assert len(request.email) == 50
    assert len(request.full_name) == 40

    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({**valid_registration(), "email": "a" * 46 + "@b.co"})
    with pytest.raises(ValidationError):
        RegisterRequest.model_validate({**valid_registration(), "full_name": "A" * 38 + " BC"})


def test_cart_quantity_is_bounded() -> None:
    assert CartItemRequest(product_id="long-public-slug-not-a-database-id", quantity=2).quantity == 2
    with pytest.raises(ValidationError):
        CartItemRequest(product_id="product-slug", quantity=100)


def test_checkout_only_accepts_nonprocessing_demo_payment() -> None:
    checkout = CheckoutRequest(
        delivery_address="Main Street 1",
        delivery_city="Quito",
        delivery_phone="0991234567",
        delivery_method="express",
        payment_method="demo",
    )
    assert checkout.payment_method == "demo"
    with pytest.raises(ValidationError):
        CheckoutRequest(
            delivery_address="Main Street 1",
            delivery_city="Quito",
            delivery_phone="0991234567",
            delivery_method="express",
            payment_method="card",
        )
