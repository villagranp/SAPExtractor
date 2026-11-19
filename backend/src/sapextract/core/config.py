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


class LocalLoginPolicy(StrEnum):
    """Quién puede iniciar sesión con usuario y contraseña locales."""

    ALL = "all"
    BREAK_GLASS_ONLY = "break_glass_only"


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

    # --- Identidad y sesiones (plan 02)
    session_idle_minutes: int = Field(30, ge=1)
    session_absolute_hours: int = Field(12, ge=1)
    session_pending_minutes: int = Field(10, ge=1, description="Vida de una sesión a medio login")
    session_cookie_secure: bool | None = Field(
        None, description="Cookie solo por HTTPS; por defecto activo salvo en dev/test"
    )
    login_max_attempts: int = Field(5, ge=1)
    login_lockout_base_minutes: int = Field(5, ge=1)
    login_lockout_max_minutes: int = Field(24 * 60, ge=1)
    auth_rate_limit_per_minute: int = Field(20, ge=1)
    mfa_required_for_admins: bool | None = Field(
        None, description="MFA obligatorio para administradores; por defecto activo en QA/PRD"
    )
    local_login_enabled_for: LocalLoginPolicy = LocalLoginPolicy.ALL
    password_min_length: int = Field(12, ge=8)
    password_history_size: int = Field(5, ge=0)
    password_denylist_file: Path | None = None
    bootstrap_admin_user: str | None = None
    bootstrap_admin_password: SecretStr | None = None

    # --- Conexiones SAP (plan 04)
    sap_catalog_ttl_minutes: int = Field(
        60, ge=1, description="Vigencia de la caché del catálogo de servicios de Gateway"
    )

    # --- Procesos de fondo
    scheduler_tick_seconds: float = 5.0
    worker_poll_seconds: float = 5.0
    spool_dir: Path = Path("spool")

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Self:
        if self.environment in (Environment.QA, Environment.PRD):
            if self.master_key is None or not self.master_key.get_secret_value():
                msg = "SX_MASTER_KEY es obligatoria en QA y PRD"
                raise ValueError(msg)
            if self.log_json is False:
                msg = "SX_LOG_JSON debe ser true en QA y PRD"
                raise ValueError(msg)
        return self

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRD

    @property
    def is_local_dev(self) -> bool:
        return self.environment in (Environment.DEV, Environment.TEST)

    @property
    def cookie_secure(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return not self.is_local_dev

    @property
    def mfa_required(self) -> bool:
        if self.mfa_required_for_admins is not None:
            return self.mfa_required_for_admins
        return not self.is_local_dev


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados para todo el proceso."""
    return Settings()  # database_url llega por entorno (SX_DATABASE_URL)
