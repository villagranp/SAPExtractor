"""Conexiones SAP: secretos cifrados genéricos, sistemas SAP y caché del catálogo de Gateway.

Revision ID: 0004_sap_connections
Revises: 0003_identity
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_sap_connections"
down_revision: str | None = "0003_identity"
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
        "secret",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(50), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(20), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        _ts("rotated_at"),
        sa.PrimaryKeyConstraint("id", name="pk_secret"),
        schema=SCHEMA,
    )
    op.create_index("ix_secret_key_version", "secret", ["key_version"], schema=SCHEMA)

    op.create_table(
        "sap_system",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("system_type", sa.String(20), nullable=False),
        sa.Column("base_url", sa.String(500), nullable=False),
        sa.Column("sap_client", sa.String(3), nullable=False),
        sa.Column("language", sa.String(2), nullable=False),
        sa.Column("auth_type", sa.String(40), nullable=False),
        sa.Column("credential_secret_id", sa.Uuid()),
        sa.Column("credential_info", postgresql.JSONB(), nullable=False),
        sa.Column("tls_verify", sa.Boolean(), nullable=False),
        sa.Column("ca_bundle_secret_id", sa.Uuid()),
        sa.Column("ca_bundle_info", postgresql.JSONB()),
        sa.Column("proxy_url", sa.String(500)),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("max_concurrent_requests", sa.Integer(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.Text()),
        _ts("last_test_at"),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("last_test_result", postgresql.JSONB()),
        sa.Column("test_outdated", sa.Boolean(), nullable=False),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_sap_system"),
        sa.ForeignKeyConstraint(
            ["credential_secret_id"],
            [f"{SCHEMA}.secret.id"],
            name="fk_sap_system_credential_secret_id_secret",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ca_bundle_secret_id"],
            [f"{SCHEMA}.secret.id"],
            name="fk_sap_system_ca_bundle_secret_id_secret",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "environment IN ('DEV', 'QAS', 'PRD')", name="ck_sap_system_environment"
        ),
        sa.CheckConstraint(
            "system_type IN ('ECC', 'S4HANA_ONPREM', 'S4HANA_CLOUD')",
            name="ck_sap_system_system_type",
        ),
        sa.CheckConstraint(
            "auth_type IN ('basic', 'x509', 'oauth2_client_credentials')",
            name="ck_sap_system_auth_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'inactive')", name="ck_sap_system_status"
        ),
        sa.CheckConstraint(
            "deleted_at IS NOT NULL OR credential_secret_id IS NOT NULL",
            name="ck_sap_system_credentials_required",
        ),
        sa.CheckConstraint(
            "timeout_seconds BETWEEN 5 AND 600 AND max_concurrent_requests BETWEEN 1 AND 32",
            name="ck_sap_system_limits",
        ),
        schema=SCHEMA,
    )
    # El código es único entre los sistemas no eliminados (la baja lógica lo libera).
    op.create_index(
        "uq_sap_system_code_active",
        "sap_system",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_sap_system_status", "sap_system", ["status"], schema=SCHEMA)

    op.create_table(
        "sap_catalog_cache",
        sa.Column("sap_system_id", sa.Uuid(), nullable=False),
        _ts("fetched_at", nullable=False),
        sa.Column("services", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("sap_system_id", name="pk_sap_catalog_cache"),
        sa.ForeignKeyConstraint(
            ["sap_system_id"],
            [f"{SCHEMA}.sap_system.id"],
            name="fk_sap_catalog_cache_sap_system_id_sap_system",
            ondelete="CASCADE",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("sap_catalog_cache", schema=SCHEMA)
    op.drop_index("ix_sap_system_status", table_name="sap_system", schema=SCHEMA)
    op.drop_index("uq_sap_system_code_active", table_name="sap_system", schema=SCHEMA)
    op.drop_table("sap_system", schema=SCHEMA)
    op.drop_index("ix_secret_key_version", table_name="secret", schema=SCHEMA)
    op.drop_table("secret", schema=SCHEMA)
