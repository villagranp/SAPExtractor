"""Configuración de la aplicación (variables de entorno con prefijo ``SX_``)."""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from sapextract.core.network import HostPolicy, parse_entry


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
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1"],
        description=(
            "IP o redes (CIDR) de los proxies inversos cuyas cabeceras X-Forwarded-For/Proto "
            "se aceptan; el resto de clientes no puede falsear su IP"
        ),
    )
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
    public_url: str | None = Field(
        None,
        description=(
            "URL pública de la plataforma (https://sapextract.empresa.local); de ella sale la "
            "URI de redirección de Entra ID. Sin ella se toma de la petición"
        ),
    )
    entra_authority_url: str = Field(
        "https://login.microsoftonline.com",
        description="Servidor de Entra ID; solo se cambia en dev y test (Entra simulado)",
    )

    # --- Conexiones SAP (plan 04)
    sap_catalog_ttl_minutes: int = Field(
        60, ge=1, description="Vigencia de la caché del catálogo de servicios de Gateway"
    )
    sap_allowed_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts SAP, proxies y servidores de tokens permitidos: sufijos DNS "
            "(empresa.local) o redes (10.0.0.0/8). Vacío = cualquiera; obligatorio en PRD"
        ),
    )

    # --- Destinos (plan 07)
    dest_allowed_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts de destino permitidos (Snowflake, SQL Server): sufijos DNS "
            "(snowflakecomputing.com) o redes. Vacío = cualquiera; obligatorio en PRD"
        ),
    )
    csv_roots: list[Path] = Field(
        default_factory=list,
        description="Carpetas raíz donde los destinos CSV pueden escribir; sin ellas no hay CSV",
    )

    # --- Procesos de fondo
    scheduler_tick_seconds: float = 5.0
    worker_poll_seconds: float = 5.0
    spool_dir: Path = Path("spool")
    worker_pools: list[str] = Field(
        default_factory=lambda: ["default"],
        description="Pools de los que este worker toma runs (p. ej. uno reservado para PRD)",
    )
    worker_heartbeat_seconds: float = Field(15.0, gt=0)
    run_stale_seconds: float = Field(
        90.0, gt=0, description="Sin latido durante este tiempo, el intento se da por caído"
    )
    scheduler_misfire_grace_seconds: float = Field(
        60.0, ge=0, description="Retraso a partir del cual un disparo cuenta como atrasado"
    )
    # --- Motor (plan 06)
    spool_keep_on_success: bool = Field(
        default=False, description="Conservar el spool tras una carga correcta (diagnóstico)"
    )
    page_timeout_seconds: int = Field(300, ge=10, le=3600, description="Timeout por página SAP")
    watermark_safety_lag_seconds: int = Field(
        120, ge=0, le=3600, description="Margen para no leer transacciones en curso (plan 06 §5)"
    )
    # --- Monitoreo y alertas (plan 09)
    alert_eval_seconds: float = Field(
        20.0, ge=1, description="Cada cuánto evalúa el scheduler las reglas de alerta"
    )
    alert_cooldown_minutes: int = Field(
        10, ge=0, le=1440, description="Una alerta que reaparece antes de este plazo no avisa"
    )
    alert_allowed_hosts: list[str] = Field(
        default_factory=list,
        description=(
            "Hosts permitidos para los webhooks de avisos (Teams, genérico): sufijos DNS o "
            "redes. Vacío = cualquiera; obligatorio en PRD"
        ),
    )
    smtp_host: str | None = Field(None, description="Servidor SMTP de los avisos por correo")
    smtp_port: int = Field(587, ge=1, le=65535)
    smtp_starttls: bool = True
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = Field(None, description="Remitente de los avisos")
    metrics_token: SecretStr | None = Field(
        None, description="Token Bearer de /metrics (Prometheus); sin él, /metrics no responde"
    )

    @model_validator(mode="after")
    def _validate_network(self) -> Self:
        for entry in [*self.sap_allowed_hosts, *self.dest_allowed_hosts, *self.alert_allowed_hosts]:
            parse_entry(entry)  # ValueError con la entrada concreta
        for proxy in self.trusted_proxies:
            if proxy.strip() == "*":
                continue
            try:
                ipaddress.ip_network(proxy.strip(), strict=False)
            except ValueError:
                msg = f"SX_TRUSTED_PROXIES: «{proxy}» no es una IP ni una red CIDR"
                raise ValueError(msg) from None
        if self.run_stale_seconds < 2 * self.worker_heartbeat_seconds:
            msg = "SX_RUN_STALE_SECONDS debe ser al menos el doble de SX_WORKER_HEARTBEAT_SECONDS"
            raise ValueError(msg)
        if not self.worker_pools:
            msg = "SX_WORKER_POOLS no puede estar vacío"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_production_requirements(self) -> Self:
        if self.environment in (Environment.QA, Environment.PRD):
            if self.master_key is None or not self.master_key.get_secret_value():
                msg = "SX_MASTER_KEY es obligatoria en QA y PRD"
                raise ValueError(msg)
            if self.log_json is False:
                msg = "SX_LOG_JSON debe ser true en QA y PRD"
                raise ValueError(msg)
            if any(p.strip() == "*" for p in self.trusted_proxies):
                msg = "SX_TRUSTED_PROXIES no puede ser '*' en QA y PRD: indique la red del proxy"
                raise ValueError(msg)
        if not self.is_local_dev:
            for name, url in (
                ("SX_PUBLIC_URL", self.public_url),
                ("SX_ENTRA_AUTHORITY_URL", self.entra_authority_url),
            ):
                if url is not None and not url.startswith("https://"):
                    msg = f"{name} debe empezar por https:// fuera de dev y test"
                    raise ValueError(msg)
        if self.environment is Environment.PRD and not self.sap_allowed_hosts:
            msg = 'SX_SAP_ALLOWED_HOSTS es obligatoria en PRD (p. ej. ["empresa.local"])'
            raise ValueError(msg)
        if self.environment is Environment.PRD and not self.dest_allowed_hosts:
            msg = 'SX_DEST_ALLOWED_HOSTS es obligatoria en PRD (p. ej. ["snowflakecomputing.com"])'
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
    def sap_host_policy(self) -> HostPolicy:
        return HostPolicy(
            allowed=tuple(self.sap_allowed_hosts),
            allow_loopback=self.is_local_dev,
            label="SX_SAP_ALLOWED_HOSTS",
        )

    @property
    def alert_host_policy(self) -> HostPolicy:
        return HostPolicy(
            allowed=tuple(self.alert_allowed_hosts),
            allow_loopback=self.is_local_dev,
            label="SX_ALERT_ALLOWED_HOSTS",
        )

    @property
    def dest_host_policy(self) -> HostPolicy:
        return HostPolicy(
            allowed=tuple(self.dest_allowed_hosts),
            allow_loopback=self.is_local_dev,
            label="SX_DEST_ALLOWED_HOSTS",
        )

    @property
    def mfa_required(self) -> bool:
        if self.mfa_required_for_admins is not None:
            return self.mfa_required_for_admins
        return not self.is_local_dev


@lru_cache
def get_settings() -> Settings:
    """Settings cacheados para todo el proceso."""
    return Settings()  # database_url llega por entorno (SX_DATABASE_URL)
