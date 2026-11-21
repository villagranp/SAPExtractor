"""Motor de extracción: pipeline mínimo, run, intentos, pasos, log y estado de delta.

Plan 06 §2 y §3.6, plan 08 §1 (reducido a la ejecución manual), ADR 0006 §5.

Revision ID: 0008_engine
Revises: 0007_identity_provider
Create Date: 2026-11-20
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_engine"
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


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column],
        [f"{SCHEMA}.{target}.id"],
        name=f"fk_{table}_{column}_{target}",
        ondelete=ondelete,
    )


def upgrade() -> None:
    _pipeline()
    _run()
    _run_attempt()
    _run_step()
    _run_log()
    _delta_state()


def _pipeline() -> None:
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
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("tags", postgresql.JSONB(), nullable=False),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_pipeline"),
        _fk("pipeline", "source_id", "source", "RESTRICT"),
        _fk("pipeline", "destination_id", "destination", "RESTRICT"),
        sa.CheckConstraint(
            "extraction_mode IN ('FULL', 'DELTA', 'WATERMARK')", name="ck_pipeline_mode"
        ),
        sa.CheckConstraint("status IN ('draft', 'active', 'paused')", name="ck_pipeline_status"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 10", name="ck_pipeline_max_attempts"),
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


def _run() -> None:
    op.create_table(
        "run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("trigger", sa.String(20), nullable=False),
        sa.Column("triggered_by", sa.String(100)),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.String(40)),
        sa.Column("delta_generation", sa.Integer()),
        sa.Column("start_token", sa.Text()),
        _ts("window_start"),
        _ts("window_end"),
        sa.Column("filters_hash", sa.String(64)),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("claimed_by", sa.String(200)),
        _ts("claimed_at"),
        _ts("heartbeat_at"),
        _ts("available_at", nullable=False, default_now=True),
        sa.Column("attempts_used", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("timeout_minutes", sa.Integer(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("cancel_requested_by", sa.String(100)),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_kind", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        sa.Column("next_token", sa.Text()),
        sa.Column("rows_extracted", sa.BigInteger()),
        sa.Column("rows_inserted", sa.BigInteger()),
        sa.Column("rows_updated", sa.BigInteger()),
        sa.Column("rows_deleted", sa.BigInteger()),
        _ts("created_at", nullable=False, default_now=True),
        _ts("started_at"),
        _ts("finished_at"),
        sa.PrimaryKeyConstraint("id", name="pk_run"),
        _fk("run", "pipeline_id", "pipeline", "RESTRICT"),
        sa.CheckConstraint(
            "mode IN ('FULL', 'DELTA_INIT', 'DELTA', 'WATERMARK')", name="ck_run_mode"
        ),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_run_status",
        ),
        sa.CheckConstraint(
            "batch_id IS NULL OR batch_id ~ '^[dwif]-[0-9a-f]{24}$'", name="ck_run_batch_id"
        ),
        schema=SCHEMA,
    )
    # Nunca dos runs vivos del mismo pipeline (obligatorio para el delta, plan 08 §3).
    op.create_index(
        "uq_run_pipeline_active",
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


def _run_attempt() -> None:
    op.create_table(
        "run_attempt",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(200), nullable=False),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        _ts("started_at", nullable=False, default_now=True),
        _ts("finished_at"),
        sa.Column("outcome", sa.String(20)),
        sa.Column("error_code", sa.String(60)),
        sa.Column("error_kind", sa.String(20)),
        sa.Column("error_message", sa.Text()),
        sa.Column("spool_worker_id", sa.String(200)),
        sa.Column("sap_requested", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_run_attempt"),
        _fk("run_attempt", "run_id", "run", "CASCADE"),
        sa.UniqueConstraint("run_id", "number", name="uq_run_attempt_run_id_number"),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('succeeded', 'failed_transient', 'failed', 'stale', "
            "'fenced', 'interrupted', 'cancelled')",
            name="ck_run_attempt_outcome",
        ),
        schema=SCHEMA,
    )


def _run_step() -> None:
    op.create_table(
        "run_step",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("step", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        _ts("started_at", nullable=False, default_now=True),
        _ts("finished_at"),
        sa.Column("metrics", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_run_step"),
        _fk("run_step", "run_id", "run", "CASCADE"),
        sa.CheckConstraint(
            "step IN ('prepare', 'extract', 'load', 'commit_delta', 'cleanup')",
            name="ck_run_step_step",
        ),
        sa.CheckConstraint(
            "status IN ('running', 'succeeded', 'failed', 'skipped')", name="ck_run_step_status"
        ),
        schema=SCHEMA,
    )
    op.create_index("ix_run_step_run_id", "run_step", ["run_id", "attempt"], schema=SCHEMA)


def _run_log() -> None:
    # Particiones mensuales por `ts`: la retención (90 días, plan 03 §4) borra particiones.
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.run_log (
            id       bigint GENERATED BY DEFAULT AS IDENTITY,
            ts       timestamptz NOT NULL DEFAULT now(),
            run_id   uuid        NOT NULL,
            attempt  integer,
            step     varchar(20),
            level    varchar(10) NOT NULL,
            message  text        NOT NULL,
            data     jsonb       NOT NULL DEFAULT '{{}}'::jsonb,
            CONSTRAINT pk_run_log PRIMARY KEY (id, ts)
        ) PARTITION BY RANGE (ts)
        """
    )
    op.execute(f"CREATE INDEX ix_run_log_run_id ON {SCHEMA}.run_log (run_id, id)")
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.run_log_ensure_partitions(months_ahead integer DEFAULT 3)
        RETURNS integer
        LANGUAGE plpgsql
        SET search_path = pg_catalog, {SCHEMA}
        AS $$
        DECLARE
            first_month date := date_trunc('month', now() AT TIME ZONE 'UTC')::date;
            month_start date;
            part_name text;
            created integer := 0;
        BEGIN
            FOR i IN 0..months_ahead LOOP
                month_start := (first_month + make_interval(months => i))::date;
                part_name := 'run_log_' || to_char(month_start, 'YYYYMM');
                IF to_regclass('{SCHEMA}.' || part_name) IS NULL THEN
                    IF EXISTS (
                        SELECT 1 FROM {SCHEMA}.run_log_default
                        WHERE ts >= month_start AND ts < month_start + interval '1 month'
                    ) THEN
                        RAISE WARNING 'Hay líneas de % en la partición DEFAULT; no se crea %',
                            month_start, part_name;
                        CONTINUE;
                    END IF;
                    EXECUTE format(
                        'CREATE TABLE {SCHEMA}.%I PARTITION OF {SCHEMA}.run_log '
                        'FOR VALUES FROM (%L) TO (%L)',
                        part_name, month_start::timestamptz,
                        (month_start + interval '1 month')::timestamptz
                    );
                    created := created + 1;
                END IF;
            END LOOP;
            RETURN created;
        END
        $$
        """
    )
    op.execute(f"CREATE TABLE {SCHEMA}.run_log_default PARTITION OF {SCHEMA}.run_log DEFAULT")
    op.execute(f"SELECT {SCHEMA}.run_log_ensure_partitions(3)")


def _delta_state() -> None:
    op.create_table(
        "delta_state",
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("current_token", sa.Text()),
        sa.Column("pending_token", sa.Text()),
        sa.Column("pending_run_id", sa.Uuid()),
        sa.Column("lease_epoch", sa.BigInteger(), nullable=False),
        sa.Column("init_filters_hash", sa.String(64)),
        sa.Column("sap_close_pending", sa.Boolean(), nullable=False),
        sa.Column("watermark_value", postgresql.JSONB()),
        _ts("initialized_at"),
        _ts("last_delta_at"),
        _ts("updated_at", nullable=False, default_now=True),
        sa.PrimaryKeyConstraint("pipeline_id", name="pk_delta_state"),
        _fk("delta_state", "pipeline_id", "pipeline", "CASCADE"),
        _fk("delta_state", "pending_run_id", "run", "SET NULL"),
        sa.CheckConstraint(
            "status IN ('NOT_INITIALIZED', 'INITIALIZING', 'INITIALIZED', 'BROKEN')",
            name="ck_delta_state_status",
        ),
        sa.CheckConstraint(
            "status <> 'INITIALIZED' OR current_token IS NOT NULL OR watermark_value IS NOT NULL",
            name="ck_delta_state_initialized_token",
        ),
        sa.CheckConstraint(
            "pending_token IS NULL OR pending_run_id IS NOT NULL",
            name="ck_delta_state_pending_owner",
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "delta_state_history",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("pipeline_id", sa.Uuid(), nullable=False),
        _ts("at", nullable=False, default_now=True),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20)),
        sa.Column("generation", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Uuid()),
        sa.Column("actor", sa.String(100)),
        sa.Column("reason", sa.Text()),
        sa.Column("token_before", sa.Text()),
        sa.Column("token_after", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_delta_state_history"),
        _fk("delta_state_history", "pipeline_id", "pipeline", "CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_delta_state_history_pipeline",
        "delta_state_history",
        ["pipeline_id", "at"],
        schema=SCHEMA,
    )


def downgrade() -> None:
    op.drop_table("delta_state_history", schema=SCHEMA)
    op.drop_table("delta_state", schema=SCHEMA)
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.run_log CASCADE")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.run_log_ensure_partitions(integer)")
    op.drop_table("run_step", schema=SCHEMA)
    op.drop_table("run_attempt", schema=SCHEMA)
    op.drop_table("run", schema=SCHEMA)
    op.drop_table("pipeline", schema=SCHEMA)
