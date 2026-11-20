"""Destinos: CSV y Snowflake (plan 07, It4a).

Revision ID: 0005_destinations
Revises: 0004_sap_connections
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_destinations"
down_revision: str | None = "0004_sap_connections"
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
        "destination",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("environment", sa.String(10), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("secret_id", sa.Uuid()),
        sa.Column("credential_info", postgresql.JSONB(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name="pk_destination"),
        sa.ForeignKeyConstraint(
            ["secret_id"],
            [f"{SCHEMA}.secret.id"],
            name="fk_destination_secret_id_secret",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("type IN ('csv', 'snowflake')", name="ck_destination_type"),
        sa.CheckConstraint(
            "environment IN ('DEV', 'QAS', 'PRD')", name="ck_destination_environment"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'active', 'inactive')", name="ck_destination_status"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_destination_code_active",
        "destination",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_destination_type", "destination", ["type"], schema=SCHEMA)


def downgrade() -> None:
    op.drop_index("ix_destination_type", table_name="destination", schema=SCHEMA)
    op.drop_index("uq_destination_code_active", table_name="destination", schema=SCHEMA)
    op.drop_table("destination", schema=SCHEMA)
