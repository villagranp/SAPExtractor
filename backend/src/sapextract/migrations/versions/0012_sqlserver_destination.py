"""Destino SQL Server (plan 07 §3, It4b): nuevo valor admitido en ``destination.type``.

Revision ID: 0012_sqlserver_destination
Revises: 0011_pipeline_test
Create Date: 2026-11-21
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012_sqlserver_destination"
down_revision: str | None = "0011_pipeline_test"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"
# 0005 la creó con la convención de nombres de los metadatos (ck_<tabla>_<nombre>).
CONSTRAINT = "ck_destination_ck_destination_type"


def _allow(types: str) -> None:
    # SQL explícito: el nombre de la restricción no pasa por la convención de nombres.
    op.execute(f"ALTER TABLE {SCHEMA}.destination DROP CONSTRAINT IF EXISTS {CONSTRAINT}")
    op.execute(f"ALTER TABLE {SCHEMA}.destination DROP CONSTRAINT IF EXISTS ck_destination_type")
    op.execute(
        f"ALTER TABLE {SCHEMA}.destination ADD CONSTRAINT {CONSTRAINT} CHECK (type IN ({types}))"
    )


def upgrade() -> None:
    _allow("'csv', 'snowflake', 'sqlserver'")


def downgrade() -> None:
    # Con destinos SQL Server registrados, bajar falla a propósito: no se borran en silencio.
    _allow("'csv', 'snowflake'")
