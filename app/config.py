from functools import lru_cache
import os
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        # Do not implicitly load local secret files; configure process environment explicitly.
        env_file=os.environ.get("APEX_ENV_FILE") or None,
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    database_url: SecretStr | None = None
    cors_origins: list[str] = Field(default_factory=list)
    auth_session_hours: int = Field(default=12, ge=1, le=168)
    password_hash_memory_kib: int = Field(default=65536, ge=19456, le=262144)

    @field_validator("cors_origins")
    @classmethod
    def validate_cors_origins(cls, origins: list[str]) -> list[str]:
        validated_origins = []
        for value in origins:
            origin = value.strip().rstrip("/")
            parsed = urlsplit(origin)
            if (
                not origin
                or origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError("CORS_ORIGINS must contain exact HTTP(S) origins")
            validated_origins.append(origin)
        return validated_origins


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
