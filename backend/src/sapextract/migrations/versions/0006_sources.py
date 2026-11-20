"""Orígenes SAP: origen y versiones de su esquema (plan 05 §1).

Revision ID: 0006_sources
Revises: 0005_destinations
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006_sources"
down_revision: str | None = "0005_destinations"
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
        "source",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sap_system_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_type", sa.String(20), nullable=False),
        sa.Column("odp_context", sa.String(10)),
        sa.Column("odp_name", sa.String(100)),
        sa.Column("service_name", sa.String(100), nullable=False),
        sa.Column("service_version", sa.Integer(), nullable=False),
        sa.Column("service_path", sa.String(300), nullable=False),
        sa.Column("entity_set", sa.String(120), nullable=False),
        sa.Column("odata_version", sa.String(5), nullable=False),
        sa.Column("supports_delta", sa.Boolean(), nullable=False),
        sa.Column("delta_method", sa.String(20), nullable=False),
        sa.Column("delta_semantics", sa.String(20)),
        sa.Column("watermark_field", sa.String(120)),
        sa.Column("watermark_kind", sa.String(20)),
        sa.Column("watermark_timezone", sa.String(60)),
        sa.Column("watermark_overlap_seconds", sa.Integer()),
        sa.Column("key_fields", postgresql.JSONB(), nullable=False),
        sa.Column("default_filters", postgresql.JSONB(), nullable=False),
        sa.Column("page_size", sa.Integer(), nullable=False),
        sa.Column("preview_enabled", sa.Boolean(), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.Text()),
        _ts("schema_checked_at"),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_source"),
        sa.ForeignKeyConstraint(
            ["sap_system_id"],
            [f"{SCHEMA}.sap_system.id"],
            name="fk_source_sap_system_id_sap_system",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "source_type IN ('ODP_EXTRACTOR', 'CDS_ODP', 'CDS_ODATA')",
            name="ck_source_source_type",
        ),
        sa.CheckConstraint(
            "odp_context IS NULL OR odp_context IN ('SAPI', 'ABAP_CDS')",
            name="ck_source_odp_context",
        ),
        sa.CheckConstraint(
            "delta_method IN ('ODQ', 'WATERMARK', 'NONE')", name="ck_source_delta_method"
        ),
        sa.CheckConstraint(
            "delta_semantics IS NULL OR delta_semantics IN "
            "('after_image', 'additive', 'before_after', 'unknown')",
            name="ck_source_delta_semantics",
        ),
        sa.CheckConstraint(
            "watermark_kind IS NULL OR watermark_kind IN ('datetime', 'date', 'decimal_timestamp')",
            name="ck_source_watermark_kind",
        ),
        sa.CheckConstraint(
            "delta_method <> 'WATERMARK' OR (watermark_field IS NOT NULL "
            "AND watermark_kind IS NOT NULL AND watermark_timezone IS NOT NULL)",
            name="ck_source_watermark_required",
        ),
        sa.CheckConstraint(
            "delta_method <> 'ODQ' OR delta_semantics IS NOT NULL",
            name="ck_source_semantics_required",
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'deprecated')", name="ck_source_status"),
        sa.CheckConstraint("page_size BETWEEN 100 AND 50000", name="ck_source_page_size"),
        schema=SCHEMA,
    )
    # El código es único entre los orígenes no eliminados (la baja lógica lo libera).
    op.create_index(
        "uq_source_code_active",
        "source",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_source_sap_system_id", "source", ["sap_system_id"], schema=SCHEMA)
    op.create_index("ix_source_status", "source", ["status"], schema=SCHEMA)

    op.create_table(
        "source_schema_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        _ts("detected_at", nullable=False),
        sa.Column("detected_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_source_schema_version"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            [f"{SCHEMA}.source.id"],
            name="fk_source_schema_version_source_id_source",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("source_id", "version", name="uq_source_schema_version_source_version"),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("source_schema_version", schema=SCHEMA)
    op.drop_index("ix_source_status", table_name="source", schema=SCHEMA)
    op.drop_index("ix_source_sap_system_id", table_name="source", schema=SCHEMA)
    op.drop_index("uq_source_code_active", table_name="source", schema=SCHEMA)
    op.drop_table("source", schema=SCHEMA)
