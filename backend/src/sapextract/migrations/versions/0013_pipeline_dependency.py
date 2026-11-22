"""Dependencias entre pipelines (plan 08 §6): programación «después de otro pipeline».

Revision ID: 0013_pipeline_dependency
Revises: 0012_sqlserver_destination
Create Date: 2026-11-21
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013_pipeline_dependency"
down_revision: str | None = "0012_sqlserver_destination"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"

TYPES_BEFORE = "type IN ('cron', 'interval', 'manual_only')"
TYPES_AFTER = "type IN ('cron', 'interval', 'manual_only', 'dependency')"
DEFINITION_BEFORE = (
    "(type = 'cron' AND cron_expression IS NOT NULL) "
    "OR (type = 'interval' AND interval_minutes BETWEEN 1 AND 44640) "
    "OR type = 'manual_only'"
)
DEFINITION_AFTER = (
    f"{DEFINITION_BEFORE} OR (type = 'dependency' AND upstream_pipeline_id IS NOT NULL)"
)


def _schedule_checks(types: str, definition: str) -> None:
    op.drop_constraint("ck_schedule_type", "schedule", schema=SCHEMA, type_="check")
    op.drop_constraint("ck_schedule_definition", "schedule", schema=SCHEMA, type_="check")
    op.create_check_constraint("ck_schedule_type", "schedule", types, schema=SCHEMA)
    op.create_check_constraint("ck_schedule_definition", "schedule", definition, schema=SCHEMA)


def upgrade() -> None:
    # Pipeline del que depende: sus ejecuciones correctas lanzan las de este.
    op.add_column("schedule", sa.Column("upstream_pipeline_id", sa.Uuid()), schema=SCHEMA)
    # Solo cuentan las ejecuciones del pipeline anterior terminadas después de este instante.
    op.add_column(
        "schedule", sa.Column("upstream_cursor_at", sa.DateTime(timezone=True)), schema=SCHEMA
    )
    op.create_foreign_key(
        "fk_schedule_upstream_pipeline_id_pipeline",
        "schedule",
        "pipeline",
        ["upstream_pipeline_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_schedule_upstream_pipeline_id",
        "schedule",
        ["upstream_pipeline_id"],
        schema=SCHEMA,
        postgresql_where=sa.text("upstream_pipeline_id IS NOT NULL"),
    )
    _schedule_checks(TYPES_AFTER, DEFINITION_AFTER)

    # Ejecución del pipeline anterior que lanzó esta.
    op.add_column("run", sa.Column("upstream_run_id", sa.Uuid()), schema=SCHEMA)
    op.create_foreign_key(
        "fk_run_upstream_run_id_run",
        "run",
        "run",
        ["upstream_run_id"],
        ["id"],
        source_schema=SCHEMA,
        referent_schema=SCHEMA,
        ondelete="SET NULL",
    )
    # El scheduler busca la última ejecución correcta de cada pipeline anterior.
    op.create_index(
        "ix_run_pipeline_succeeded",
        "run",
        ["pipeline_id", "finished_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'succeeded'"),
    )


def downgrade() -> None:
    op.drop_index("ix_run_pipeline_succeeded", table_name="run", schema=SCHEMA)
    op.drop_constraint("fk_run_upstream_run_id_run", "run", schema=SCHEMA, type_="foreignkey")
    op.drop_column("run", "upstream_run_id", schema=SCHEMA)

    op.execute(f"DELETE FROM {SCHEMA}.schedule WHERE type = 'dependency'")
    _schedule_checks(TYPES_BEFORE, DEFINITION_BEFORE)
    op.drop_index("ix_schedule_upstream_pipeline_id", table_name="schedule", schema=SCHEMA)
    op.drop_constraint(
        "fk_schedule_upstream_pipeline_id_pipeline", "schedule", schema=SCHEMA, type_="foreignkey"
    )
    op.drop_column("schedule", "upstream_cursor_at", schema=SCHEMA)
    op.drop_column("schedule", "upstream_pipeline_id", schema=SCHEMA)
