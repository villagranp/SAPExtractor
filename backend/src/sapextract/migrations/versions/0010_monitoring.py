"""Monitoreo y alertas: reglas, canales, suscripciones, alertas y avisos (plan 09 §5).

Crea las reglas por defecto que el plan 09 §3 da como activas.

Revision ID: 0010_monitoring
Revises: 0009_engine
Create Date: 2026-11-21
"""

import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_monitoring"
down_revision: str | None = "0009_engine"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"

# (tipo, nombre, gravedad, parámetros): las reglas que el plan 09 §3 deja activas por defecto.
DEFAULT_RULES: tuple[tuple[str, str, str, dict[str, int]], ...] = (
    ("run_failed", "Run fallido", "critical", {}),
    ("delta_broken", "Delta roto", "critical", {}),
    ("stale_subscription", "Suscripción estancada", "warning", {"days": 7}),
    ("unclosed_subscription", "Suscripción sin cerrar", "warning", {"days": 3}),
    ("connection_down", "Conexión caída", "critical", {}),
    ("no_workers", "Sin workers", "critical", {}),
)


def _ts(name: str, *, nullable: bool = True, default_now: bool = False) -> sa.Column[object]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.func.now() if default_now else None,
    )


def _fk(table: str, column: str, target: str, ondelete: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        [column], [f"{SCHEMA}.{target}.id"], name=f"fk_{table}_{column}_{target}", ondelete=ondelete
    )


def upgrade() -> None:
    rules = op.create_table(
        "alert_rule",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("scope", sa.String(20), nullable=False),
        sa.Column("scope_value", sa.String(100)),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_alert_rule"),
        sa.CheckConstraint(
            "type IN ('run_failed', 'delta_broken', 'freshness', 'duration_anomaly', "
            "'volume_anomaly', 'stale_subscription', 'unclosed_subscription', "
            "'connection_down', 'no_workers')",
            name="ck_alert_rule_type",
        ),
        sa.CheckConstraint("scope IN ('global', 'pipeline', 'tag')", name="ck_alert_rule_scope"),
        sa.CheckConstraint(
            "severity IN ('critical', 'warning', 'info')", name="ck_alert_rule_severity"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "notification_channel",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("type", sa.String(20), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("secret_id", sa.Uuid()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        _ts("last_test_at"),
        sa.Column("last_test_ok", sa.Boolean()),
        sa.Column("last_test_error", sa.Text()),
        _ts("deleted_at"),
        sa.Column("version", sa.Integer(), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        _ts("updated_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.Column("updated_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_notification_channel"),
        _fk("notification_channel", "secret_id", "secret", "SET NULL"),
        sa.CheckConstraint(
            "type IN ('email', 'teams', 'webhook')", name="ck_notification_channel_type"
        ),
        schema=SCHEMA,
    )
    op.create_table(
        "alert_subscription",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid()),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("min_severity", sa.String(10), nullable=False),
        _ts("created_at", nullable=False, default_now=True),
        sa.Column("created_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_alert_subscription"),
        _fk("alert_subscription", "rule_id", "alert_rule", "CASCADE"),
        _fk("alert_subscription", "channel_id", "notification_channel", "CASCADE"),
        schema=SCHEMA,
    )
    op.create_index(
        "ix_alert_subscription_channel_id", "alert_subscription", ["channel_id"], schema=SCHEMA
    )
    op.create_table(
        "alert",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("rule_id", sa.Uuid(), nullable=False),
        sa.Column("type", sa.String(30), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("resource_type", sa.String(30), nullable=False),
        sa.Column("resource_id", sa.String(100), nullable=False),
        sa.Column("resource_name", sa.String(200), nullable=False),
        sa.Column("pipeline_id", sa.Uuid()),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("details", postgresql.JSONB(), nullable=False),
        _ts("first_seen_at", nullable=False),
        _ts("last_seen_at", nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.Column("acknowledged_by", sa.String(100)),
        _ts("acknowledged_at"),
        _ts("resolved_at"),
        sa.Column("resolved_by", sa.String(100)),
        sa.PrimaryKeyConstraint("id", name="pk_alert"),
        _fk("alert", "rule_id", "alert_rule", "CASCADE"),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'resolved')", name="ck_alert_status"
        ),
        schema=SCHEMA,
    )
    # Un incidente vivo por regla y recurso: la evaluación actualiza, no duplica.
    op.create_index(
        "uq_alert_live",
        "alert",
        ["rule_id", "resource_id"],
        unique=True,
        schema=SCHEMA,
        postgresql_where=sa.text("status IN ('open', 'acknowledged')"),
    )
    op.create_index("ix_alert_status_seen", "alert", ["status", "last_seen_at"], schema=SCHEMA)
    op.create_table(
        "notification",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("alert_id", sa.Uuid()),
        sa.Column("channel_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(20), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("error", sa.Text()),
        _ts("sent_at", nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_notification"),
        _fk("notification", "alert_id", "alert", "CASCADE"),
        _fk("notification", "channel_id", "notification_channel", "CASCADE"),
        schema=SCHEMA,
    )
    op.create_index("ix_notification_alert_id", "notification", ["alert_id"], schema=SCHEMA)
    # Panel: runs terminados por fecha (KPIs del día, línea de tiempo, frescura).
    op.create_index(
        "ix_run_finished",
        "run",
        ["finished_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )
    op.create_index(
        "ix_run_pipeline_finished",
        "run",
        ["pipeline_id", "finished_at"],
        schema=SCHEMA,
        postgresql_where=sa.text("finished_at IS NOT NULL"),
    )
    op.bulk_insert(
        rules,
        [
            {
                "id": uuid.uuid4(),
                "name": name,
                "type": type_,
                "scope": "global",
                "scope_value": None,
                "params": params,
                "severity": severity,
                "enabled": True,
                "version": 1,
                "created_by": "system",
                "updated_by": "system",
            }
            for type_, name, severity, params in DEFAULT_RULES
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_run_pipeline_finished", table_name="run", schema=SCHEMA)
    op.drop_index("ix_run_finished", table_name="run", schema=SCHEMA)
    op.drop_table("notification", schema=SCHEMA)
    op.drop_table("alert", schema=SCHEMA)
    op.drop_table("alert_subscription", schema=SCHEMA)
    op.drop_table("notification_channel", schema=SCHEMA)
    op.drop_table("alert_rule", schema=SCHEMA)
