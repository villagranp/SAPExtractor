"""Configuración de la aplicación (variables de entorno con prefijo ``SX_``)."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    TEST = "test"
    QA = "qa"
    PRD = "prd"


class Settings(BaseSettings):
    """Configuración validada al arrancar: si falta algo, el proceso falla de inmediato."""

    model_config = SettingsConfigDict(
        env_prefix="SX_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    app_name: str = "SAPExtract"
    build_commit: str = "local"

    # --- Base de metadatos
    database_url: str = Field(
        description="URL SQLAlchemy, p. ej. postgresql+psycopg://usuario:clave@host:5432/sapextract",
    )
    db_pool_size: int = 5
    db_max_overflow: int = 10
    db_echo: bool = False

    # --- API
    api_prefix: str = "/api/v1"
    docs_enabled: bool = True
    cors_origins: list[str] = Field(default_factory=list)
    max_request_body_bytes: int = 10 * 1024 * 1024

    # --- Logs
    log_level: str = "INFO"
    log_json: bool = True

    # --- Cifrado de secretos (cifrado envelope, ver core/security/crypto.py)
    master_key: SecretStr | None = None
    master_key_id: str = "k1"
    previous_master_keys: dict[str, SecretStr] = Field(default_factory=dict)

    # --- Procesos de fondo
    scheduler_tick_seconds: float = 5.0
    worker_poll_seconds: float = 5.0
    spool_dir: Path = Path("spool")

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Self:
        if self.environment in (Environment.QA, Environment.PRD):
            if self.master_key is None:
                msg = "SX_MASTER_KEY es obligatoria en QA y PRD"
                raise ValueError(msg)
            if self.log_json is False:
                msg = "SX_LOG_JSON debe ser true en QA y PRD"
                raise ValueError(msg)
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRD


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados para todo el proceso."""
    return Settings()  # database_url llega por entorno (SX_DATABASE_URL)
