"""Proveedor de identidad (Entra ID) y mapeo de grupos a roles (plan 02 §1, It7a).

Revision ID: 0007_identity_provider
Revises: 0006_sources
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_identity_provider"
down_revision: str | None = "0006_sources"
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
        "identity_provider",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("client_id", sa.String(36), nullable=False),
        sa.Column("credential_type", sa.String(20)),
        sa.Column("secret_id", sa.Uuid()),
        _ts("credential_expires_at"),
        _ts("credential_updated_at"),
        sa.Column("certificate_subject", sa.String(500)),
        sa.Column("certificate_thumbprint", sa.String(64)),
        sa.Column("jit_provisioning", sa.Boolean(), nullable=False),
        sa.Column("default_role_id", sa.Uuid()),
        sa.Column("allowed_domains", postgresql.JSONB(), nullable=False),
        sa.Column("require_group_membership", sa.Boolean(), nullable=False),
        _ts("last_test_at"),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("last_test_result", postgresql.JSONB()),
        sa.Column("last_test_fingerprint", sa.String(64)),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_identity_provider"),
        sa.UniqueConstraint("type", name="uq_identity_provider_type"),
        sa.ForeignKeyConstraint(
            ["secret_id"],
            [f"{SCHEMA}.secret.id"],
            name="fk_identity_provider_secret_id_secret",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["default_role_id"],
            [f"{SCHEMA}.role.id"],
            name="fk_identity_provider_default_role_id_role",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("type IN ('entra_id')", name="ck_identity_provider_type"),
        sa.CheckConstraint(
            "credential_type IS NULL OR credential_type IN ('client_secret', 'certificate')",
            name="ck_identity_provider_credential_type",
        ),
        sa.CheckConstraint(
            "(credential_type IS NULL) = (secret_id IS NULL)",
            name="ck_identity_provider_credential_secret",
        ),
        sa.CheckConstraint(
            "NOT enabled OR secret_id IS NOT NULL", name="ck_identity_provider_enabled_credential"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "idp_group_mapping",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("identity_provider_id", sa.Uuid(), nullable=False),
        sa.Column("group_object_id", sa.String(36), nullable=False),
        sa.Column("group_name", sa.String(256), nullable=False),
        sa.Column("role_id", sa.Uuid(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_idp_group_mapping"),
        sa.ForeignKeyConstraint(
            ["identity_provider_id"],
            [f"{SCHEMA}.identity_provider.id"],
            name="fk_idp_group_mapping_identity_provider_id_identity_provider",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["role_id"],
            [f"{SCHEMA}.role.id"],
            name="fk_idp_group_mapping_role_id_role",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "identity_provider_id",
            "group_object_id",
            "role_id",
            name="uq_idp_group_mapping_group_role",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_idp_group_mapping_role_id", "idp_group_mapping", ["role_id"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_idp_group_mapping_role_id", table_name="idp_group_mapping", schema=SCHEMA)
    op.drop_table("idp_group_mapping", schema=SCHEMA)
    op.drop_table("identity_provider", schema=SCHEMA)
