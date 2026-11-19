"""Identidad: usuarios, credenciales locales, roles, permisos, sesiones y tokens API.

El catálogo de permisos y los roles de sistema se sincronizan desde el código en cada
``sapextract db upgrade`` (no se siembran aquí para no duplicar la fuente de verdad).

Revision ID: 0003_identity
Revises: 0002_audit
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_identity"
down_revision: str | None = "0002_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def upgrade() -> None:
    op.create_table(
        "user_account",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("username", sa.String(100), nullable=False),
        sa.Column("email", sa.String(320)),
        sa.Column("display_name", sa.String(200), nullable=False),
        sa.Column("auth_source", sa.String(20), nullable=False),
        sa.Column("external_id", sa.String(100)),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("is_break_glass", sa.Boolean(), nullable=False),
        _ts("last_login_at"),
        sa.Column("failed_attempts", sa.Integer(), nullable=False),
        _ts("locked_until"),
        sa.Column("must_change_password", sa.Boolean(), nullable=False),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_user_account"),
        sa.UniqueConstraint("username", name="uq_user_account_username"),
        sa.UniqueConstraint("external_id", name="uq_user_account_external_id"),
        sa.CheckConstraint("auth_source IN ('local', 'entra')", name="ck_user_account_auth_source"),
        sa.CheckConstraint(
            "status IN ('invited', 'active', 'locked', 'disabled')", name="ck_user_account_status"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "local_credential",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        _ts("password_changed_at", nullable=False, default_now=True),
        sa.Column("password_history", postgresql.JSONB(), nullable=False),
        sa.Column("totp_secret", sa.Text()),
        sa.Column("totp_pending_secret", sa.Text()),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False),
        sa.Column("totp_last_step", sa.BigInteger()),
        sa.PrimaryKeyConstraint("user_id", name="pk_local_credential"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user_account.id"],
            name="fk_local_credential_user_id_user_account",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "permission",
        sa.Column("code", sa.String(100), nullable=False),
        sa.Column("module", sa.String(50), nullable=False),
        sa.Column("description", sa.String(300), nullable=False),
        sa.PrimaryKeyConstraint("code", name="pk_permission"),
        schema=SCHEMA,
    )
    op.create_table(
        "role",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(50), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("description", sa.String(500)),
        sa.Column("is_system", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_role"),
        sa.UniqueConstraint("code", name="uq_role_code"),
        sa.UniqueConstraint("name", name="uq_role_name"),
        schema=SCHEMA,
    )
    op.create_table(
        "role_permission",
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("permission_code", sa.String(100), nullable=False),
        sa.PrimaryKeyConstraint("role_id", "permission_code", name="pk_role_permission"),
        sa.ForeignKeyConstraint(
            ["role_id"],
            [f"{SCHEMA}.role.id"],
            name="fk_role_permission_role_id_role",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["permission_code"],
            [f"{SCHEMA}.permission.code"],
            name="fk_role_permission_permission_code_permission",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "user_role",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("assigned_by", sa.String(100)),
        _ts("assigned_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("user_id", "role_id", name="pk_user_role"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user_account.id"],
            name="fk_user_role_user_id_user_account",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            [f"{SCHEMA}.role.id"],
            name="fk_user_role_role_id_role",
            ondelete="RESTRICT",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_user_role_role_id", "user_role", ["role_id"], schema=SCHEMA)
    op.create_table(
        "user_session",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("csrf_hash", sa.String(64), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(20), nullable=False),
        sa.Column("auth_method", sa.String(20), nullable=False),
        _ts("created_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        _ts("expires_at", nullable=False),
        sa.Column("ip", sa.String(45)),
        sa.Column("user_agent", sa.String(300)),
        _ts("revoked_at"),
        sa.Column("revoked_reason", sa.String(50)),
        sa.PrimaryKeyConstraint("id", name="pk_user_session"),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user_account.id"],
            name="fk_user_session_user_id_user_account",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_user_session_user_id", "user_session", ["user_id"], schema=SCHEMA)
    op.create_table(
        "api_token",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_prefix", sa.String(20), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", postgresql.JSONB(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        _ts("expires_at", nullable=False),
        _ts("last_used_at"),
        _ts("revoked_at"),
        sa.PrimaryKeyConstraint("id", name="pk_api_token"),
        sa.UniqueConstraint("token_hash", name="uq_api_token_token_hash"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            [f"{SCHEMA}.user_account.id"],
            name="fk_api_token_user_id_user_account",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_api_token_user_id", "api_token", ["user_id"], schema=SCHEMA)


def downgrade() -> None:
    for table in (
        "api_token",
        "user_session",
        "user_role",
        "role_permission",
        "role",
        "permission",
        "local_credential",
        "user_account",
    ):
        op.drop_table(table, schema=SCHEMA)
