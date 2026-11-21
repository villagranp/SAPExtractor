"""Orquestación: pipelines, programación, cola de runs, intentos y workers (plan 08 §1, It6).

``run`` y ``run_attempt`` llevan los campos del ADR 0006 §5. ``run_step`` y ``delta_state``
los crea el motor (It5).

Revision ID: 0008_orchestration
Revises: 0007_identity_provider
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_orchestration"
down_revision: str | None = "0007_identity_provider"
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
    # Límite de runs simultáneos contra un sistema SAP (plan 08 §3).
    op.add_column(
        "sap_system",
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=False, server_default="2"),
        schema=SCHEMA,
    )
    op.create_check_constraint(
        "ck_sap_system_max_concurrent_runs",
        "sap_system",
        "max_concurrent_runs BETWEEN 1 AND 16",
        schema=SCHEMA,
    )

    op.create_table(
        "concurrency_group",
        sa.Column("code", sa.String(30), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("max_parallel", sa.Integer(), nullable=False),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("code", name="pk_concurrency_group"),
        sa.CheckConstraint(
            "max_parallel BETWEEN 1 AND 64", name="ck_concurrency_group_max_parallel"
        ),
        schema=SCHEMA,
    )

    op.create_table(
        "pipeline",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(40), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=False),
        sa.Column("extraction_mode", sa.String(20), nullable=False),
        sa.Column("load_options", postgresql.JSONB(), nullable=False),
        sa.Column("column_mapping", postgresql.JSONB(), nullable=False),
        # NULL: se usan los filtros por defecto del origen.
        sa.Column("filters", postgresql.JSONB()),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("retry_policy", postgresql.JSONB(), nullable=False),
        sa.Column("concurrency_group", sa.String(30)),
        sa.Column("worker_pool", sa.String(30), nullable=False),
        sa.Column("log_level", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("status_reason", sa.Text()),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        sa.Column("owner_id", sa.Uuid()),
        sa.Column("cloned_from_id", sa.Uuid()),
        # Motivo con el que se confirmó un pipeline cuyos entornos no coinciden.
        sa.Column("cross_environment_reason", sa.Text()),
        _ts("activated_at"),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline"),
        sa.ForeignKeyConstraint(
            ["source_id"],
            [f"{SCHEMA}.source.id"],
            name="fk_pipeline_source_id_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            [f"{SCHEMA}.destination.id"],
            name="fk_pipeline_destination_id_destination",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["concurrency_group"],
            [f"{SCHEMA}.concurrency_group.code"],
            name="fk_pipeline_concurrency_group_concurrency_group",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            [f"{SCHEMA}.user_account.id"],
            name="fk_pipeline_owner_id_user_account",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["cloned_from_id"],
            [f"{SCHEMA}.pipeline.id"],
            name="fk_pipeline_cloned_from_id_pipeline",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "extraction_mode IN ('FULL', 'DELTA', 'WATERMARK')", name="ck_pipeline_extraction_mode"
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'paused')", name="ck_pipeline_status"),
        sa.CheckConstraint("priority BETWEEN 1 AND 5", name="ck_pipeline_priority"),
        sa.CheckConstraint("timeout_minutes BETWEEN 1 AND 2880", name="ck_pipeline_timeout"),
        sa.CheckConstraint(
            "log_level IN ('DEBUG', 'INFO', 'WARNING', 'ERROR')", name="ck_pipeline_log_level"
        ),
        schema=SCHEMA,
    )
    op.create_index(
        "uq_pipeline_code_active",
        "pipeline",
        ["code"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index("ix_pipeline_source_id", "pipeline", ["source_id"], schema=SCHEMA)
    op.create_index("ix_pipeline_destination_id", "pipeline", ["destination_id"], schema=SCHEMA)
    op.create_index("ix_pipeline_status", "pipeline", ["status"], schema=SCHEMA)

    op.create_table(
        "schedule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("cron_expression", sa.String(120)),
        sa.Column("interval_minutes", sa.Integer()),
        sa.Column("timezone", sa.String(60), nullable=False),
        _ts("start_at"),
        _ts("end_at"),
        sa.Column("blackout_windows", postgresql.JSONB(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("misfire_policy", sa.String(10), nullable=False),
        _ts("next_fire_at"),
        _ts("last_fire_at"),
        # Última ejecución omitida (blackout, run ya en curso, disparo atrasado).
        _ts("last_skip_at"),
        sa.Column("last_skip_reason", sa.String(30)),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_schedule"),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            [f"{SCHEMA}.pipeline.id"],
            name="fk_schedule_pipeline_id_pipeline",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint("type IN ('cron', 'interval', 'manual_only')", name="ck_schedule_type"),
        sa.CheckConstraint(
            "misfire_policy IN ('skip', 'run_once')", name="ck_schedule_misfire_policy"
        ),
        sa.CheckConstraint(
            "(type = 'cron' AND cron_expression IS NOT NULL) "
            "OR (type = 'interval' AND interval_minutes BETWEEN 1 AND 44640) "
            "OR type = 'manual_only'",
            name="ck_schedule_definition",
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_schedule_pipeline_id", "schedule", ["pipeline_id"], schema=SCHEMA)
    op.create_index(
        "ix_schedule_next_fire_at",
        "schedule",
        ["next_fire_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("enabled AND next_fire_at IS NOT NULL"),
    )

    op.create_table(
        "worker",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("hostname", sa.String(200), nullable=False),
        sa.Column("pid", sa.Integer(), nullable=False),
        sa.Column("pools", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("has_executor", sa.Boolean(), nullable=False),
        sa.Column("current_run_id", sa.Uuid()),
        _ts("started_at", nullable=False),
        _ts("heartbeat_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_worker"),
        sa.CheckConstraint("status IN ('online', 'stopping', 'offline')", name="ck_worker_status"),
        schema=SCHEMA,
    )

    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("triggered_by", sa.String(100)),
        sa.Column("schedule_id", sa.Uuid()),
        sa.Column("reason", sa.Text()),
        sa.Column("priority", sa.Integer(), nullable=False),
        _ts("available_at", nullable=False),
        # Intentos fallidos que el run puede gastar (política del pipeline + reintentos manuales).
        sa.Column("attempts_budget", sa.Integer(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("claimed_by", sa.Uuid()),
        _ts("claimed_at"),
        _ts("heartbeat_at"),
        _ts("started_at"),
        _ts("finished_at"),
        _ts("cancel_requested_at"),
        sa.Column("cancel_requested_by", sa.String(100)),
        # Identidad del lote: la fija el motor en el primer intento (ADR 0006 §4).
        sa.Column("batch_id", sa.String(30)),
        sa.Column("delta_generation", sa.Integer()),
        sa.Column("start_token", sa.Text()),
        _ts("window_start"),
        _ts("window_end"),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_kind", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        _ts("created_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("id", name="pk_run"),
        sa.ForeignKeyConstraint(
            ["pipeline_id"],
            [f"{SCHEMA}.pipeline.id"],
            name="fk_run_pipeline_id_pipeline",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["schedule_id"],
            [f"{SCHEMA}.schedule.id"],
            name="fk_run_schedule_id_schedule",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "mode IN ('FULL', 'DELTA_INIT', 'DELTA', 'WATERMARK')", name="ck_run_mode"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        sa.CheckConstraint(
            "trigger IN ('schedule', 'manual', 'api', 'dependency')", name="ck_run_trigger"
        ),
        schema=SCHEMA,
    )
    # Un pipeline tiene como máximo un run vivo (en cola o en curso): obligatorio para el delta.
    op.create_index(
        "uq_run_pipeline_live",
        "run",
        ["pipeline_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_run_queue",
        "run",
        ["priority", "available_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index("ix_run_pipeline_created", "run", ["pipeline_id", "created_at"], schema=SCHEMA)
    op.create_index("ix_run_status_created", "run", ["status", "created_at"], schema=SCHEMA)

    op.create_table(
        "run_attempt",
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.Uuid(), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        _ts("started_at", nullable=False),
        _ts("finished_at"),
        sa.Column("outcome", sa.String(20)),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_kind", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        # Worker que conserva un spool válido del lote; lo escribe el motor (ADR 0006 §5).
        sa.Column("spool_worker_id", sa.Uuid()),
        sa.PrimaryKeyConstraint("run_id", "number", name="pk_run_attempt"),
        sa.ForeignKeyConstraint(
            ["run_id"],
            [f"{SCHEMA}.run.id"],
            name="fk_run_attempt_run_id_run",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed_transient', 'failed', "
            "'stale', 'fenced', 'interrupted', 'cancelled')",
            name="ck_run_attempt_outcome",
        ),
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("run_attempt", schema=SCHEMA)
    op.drop_index("ix_run_status_created", table_name="run", schema=SCHEMA)
    op.drop_index("ix_run_pipeline_created", table_name="run", schema=SCHEMA)
    op.drop_index("ix_run_queue", table_name="run", schema=SCHEMA)
    op.drop_index("uq_run_pipeline_live", table_name="run", schema=SCHEMA)
    op.drop_table("run", schema=SCHEMA)
    op.drop_table("worker", schema=SCHEMA)
    op.drop_index("ix_schedule_next_fire_at", table_name="schedule", schema=SCHEMA)
    op.drop_index("ix_schedule_pipeline_id", table_name="schedule", schema=SCHEMA)
    op.drop_table("schedule", schema=SCHEMA)
    op.drop_index("ix_pipeline_status", table_name="pipeline", schema=SCHEMA)
    op.drop_index("ix_pipeline_destination_id", table_name="pipeline", schema=SCHEMA)
    op.drop_index("ix_pipeline_source_id", table_name="pipeline", schema=SCHEMA)
    op.drop_index("uq_pipeline_code_active", table_name="pipeline", schema=SCHEMA)
    op.drop_table("pipeline", schema=SCHEMA)
    op.drop_table("concurrency_group", schema=SCHEMA)
    op.drop_constraint(
        "ck_sap_system_max_concurrent_runs", "sap_system", schema=SCHEMA, type_="check"
    )
    op.drop_column("sap_system", "max_concurrent_runs", schema=SCHEMA)
