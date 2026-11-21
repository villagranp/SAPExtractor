"""Tablas del módulo de identidad (plan 02 §1)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sapextract.core.db import AuthorMixin, Base, IdMixin, TimestampMixin


class UserAccount(IdMixin, TimestampMixin, AuthorMixin, Base):
    """Usuario de la plataforma (``user`` es palabra reservada en PostgreSQL)."""

    __tablename__ = "user_account"

    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    auth_source: Mapped[str] = mapped_column(String(20), nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    is_break_glass: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    credential: Mapped[LocalCredential | None] = relationship(
        back_populates="user", cascade="all, delete-orphan", uselist=False
    )
    role_links: Mapped[list[UserRole]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        foreign_keys="UserRole.user_id",
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def roles(self) -> list[Role]:
        return sorted((link.role for link in self.role_links), key=lambda r: r.name)


class LocalCredential(Base):
    __tablename__ = "local_credential"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    password_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    password_history: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    totp_secret: Mapped[str | None] = mapped_column(Text)  # cifrado (SecretCipher)
    totp_pending_secret: Mapped[str | None] = mapped_column(Text)  # cifrado, aún sin confirmar
    totp_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    totp_last_step: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[UserAccount] = relationship(back_populates="credential")


class Permission(Base):
    __tablename__ = "permission"

    code: Mapped[str] = mapped_column(String(100), primary_key=True)
    module: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str] = mapped_column(String(300), nullable=False)


class Role(IdMixin, TimestampMixin, AuthorMixin, Base):
    __tablename__ = "role"

    code: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    permission_links: Mapped[list[RolePermission]] = relationship(
        back_populates="role", cascade="all, delete-orphan"
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012

    @property
    def permission_codes(self) -> list[str]:
        return sorted(link.permission_code for link in self.permission_links)


class RolePermission(Base):
    __tablename__ = "role_permission"

    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("role.id", ondelete="CASCADE"), primary_key=True
    )
    permission_code: Mapped[str] = mapped_column(
        ForeignKey("permission.code", ondelete="CASCADE"), primary_key=True
    )

    role: Mapped[Role] = relationship(back_populates="permission_links")


class UserRole(Base):
    __tablename__ = "user_role"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), primary_key=True
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT"), primary_key=True, index=True
    )
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    assigned_by: Mapped[str | None] = mapped_column(String(100))
    assigned_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    user: Mapped[UserAccount] = relationship(back_populates="role_links", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(lazy="joined")


class UserSession(IdMixin, Base):
    """Sesión de servidor. Solo se guarda el hash del token, nunca el token."""

    __tablename__ = "user_session"

    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    csrf_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    auth_method: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(45))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_reason: Mapped[str | None] = mapped_column(String(50))

    user: Mapped[UserAccount] = relationship(lazy="joined")


class ApiToken(IdMixin, Base):
    __tablename__ = "api_token"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("user_account.id", ondelete="CASCADE"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_prefix: Mapped[str] = mapped_column(String(20), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[UserAccount] = relationship(lazy="joined")

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "prefix": self.token_prefix, "scopes": sorted(self.scopes)}


class IdentityProvider(IdMixin, TimestampMixin, AuthorMixin, Base):
    """Proveedor de identidad externo (hoy solo Entra ID: una fila por tipo)."""

    __tablename__ = "identity_provider"

    type: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    client_id: Mapped[str] = mapped_column(String(36), nullable=False)
    credential_type: Mapped[str | None] = mapped_column(String(20))
    secret_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("secret.id", ondelete="RESTRICT")
    )
    # Caducidad del secreto (la indica el admin) o del certificado (se lee de él).
    credential_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    credential_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_subject: Mapped[str | None] = mapped_column(String(500))
    certificate_thumbprint: Mapped[str | None] = mapped_column(String(64))
    jit_provisioning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    default_role_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT")
    )
    allowed_domains: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    require_group_membership: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_ok: Mapped[bool | None] = mapped_column(Boolean)
    last_test_result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # Huella de inquilino, client ID y credencial probados (ver domain/entra.py).
    last_test_fingerprint: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    default_role: Mapped[Role | None] = relationship(lazy="joined")
    group_mappings: Mapped[list[IdpGroupMapping]] = relationship(
        back_populates="provider",
        cascade="all, delete-orphan",
        order_by="IdpGroupMapping.group_name",
    )

    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012


class IdpGroupMapping(IdMixin, Base):
    """Grupo de seguridad del proveedor que concede un rol en cada inicio de sesión."""

    __tablename__ = "idp_group_mapping"

    identity_provider_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity_provider.id", ondelete="CASCADE"), nullable=False
    )
    group_object_id: Mapped[str] = mapped_column(String(36), nullable=False)
    group_name: Mapped[str] = mapped_column(String(256), nullable=False)
    role_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("role.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    created_by: Mapped[str | None] = mapped_column(String(100))

    provider: Mapped[IdentityProvider] = relationship(back_populates="group_mappings")
    role: Mapped[Role] = relationship(lazy="joined")

    __table_args__ = (
        UniqueConstraint(
            "identity_provider_id",
            "group_object_id",
            "role_id",
            name="uq_idp_group_mapping_group_role",
        ),
    )
