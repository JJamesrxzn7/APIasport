import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

ShortText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
EmailText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=50)]
PasswordText = Annotated[str, StringConstraints(min_length=12, max_length=256)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=40)]
    email: EmailText
    password: PasswordText
    identification: Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=13)]
    city_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=3)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value

    @field_validator("identification")
    @classmethod
    def identification_digits_only(cls, value: str) -> str:
        digits = re.sub(r"\D", "", value)
        if len(digits) != len(value) or len(digits) not in range(7, 14):
            raise ValueError("Identification must contain digits only")
        return digits


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: EmailText
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class AdminInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailText

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value


class AdminRegistrationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invitation_token: Annotated[str, StringConstraints(strip_whitespace=True, min_length=32, max_length=128)]
    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    password: PasswordText


class AdminBootstrapRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=120)]
    email: EmailText
    password: PasswordText

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Invalid email address")
        return value


class CartItemRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    quantity: int = Field(ge=1, le=99)


class CheckoutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    delivery_address: Annotated[str, StringConstraints(strip_whitespace=True, min_length=4, max_length=120)]
    delivery_city: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]
    delivery_phone: Annotated[str, StringConstraints(strip_whitespace=True, min_length=7, max_length=20)]
    delivery_reference: Annotated[str | None, StringConstraints(strip_whitespace=True, max_length=120)] = None
    delivery_method: Literal["estandar", "express"] = "estandar"
    payment_method: Literal["demo"] = "demo"
