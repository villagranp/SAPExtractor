"""Ejecución de prueba del pipeline (plan 08 §5): resultado y huella de lo probado.

Revision ID: 0011_pipeline_test
Revises: 0010_monitoring
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_pipeline_test"
down_revision: str | None = "0010_monitoring"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"
COLUMNS = ("last_test_fingerprint", "last_test_result", "last_test_ok", "last_test_at")


def upgrade() -> None:
    op.add_column("pipeline", sa.Column("last_test_at", sa.DateTime(timezone=True)), schema=SCHEMA)
    op.add_column("pipeline", sa.Column("last_test_ok", sa.Boolean()), schema=SCHEMA)
    op.add_column("pipeline", sa.Column("last_test_result", postgresql.JSONB()), schema=SCHEMA)
    # Huella de la configuración probada: si cambia, la prueba deja de estar vigente.
    op.add_column("pipeline", sa.Column("last_test_fingerprint", sa.String(64)), schema=SCHEMA)


def downgrade() -> None:
    for column in COLUMNS:
        op.drop_column("pipeline", column, schema=SCHEMA)
