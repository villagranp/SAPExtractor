"""Esquema base: tabla de ajustes globales de la plataforma.

Revision ID: 0001_base_schema
Revises:
Create Date: 2026-11-18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_base_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"


def upgrade() -> None:
    op.create_table(
        "system_setting",
        sa.Column("key", sa.String(100), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("updated_by", sa.String(100), nullable=True),
        sa.PrimaryKeyConstraint("key", name="pk_system_setting"),
        schema=SCHEMA,
    )
    op.bulk_insert(
        sa.table(
            "system_setting",
            sa.column("key", sa.String),
            sa.column("value", postgresql.JSONB),
            sa.column("description", sa.Text),
            schema=SCHEMA,
        ),
        [
            {
                "key": "platform.timezone",
                "value": "UTC",
                "description": "Zona horaria por defecto de programaciones y pantallas",
            },
            {
                "key": "platform.language",
                "value": "es",
                "description": "Idioma por defecto de la interfaz",
            },
        ],
    )


def downgrade() -> None:
    op.drop_table("system_setting", schema=SCHEMA)
