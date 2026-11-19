"""Auditoría: tabla de solo inserción, particionada por mes y protegida por triggers.

Revision ID: 0002_audit
Revises: 0001_base_schema
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0002_audit"
down_revision: str | None = "0001_base_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "sx"


def upgrade() -> None:
    op.execute(f"CREATE SEQUENCE {SCHEMA}.audit_event_seq")
    op.execute(
        f"""
        CREATE TABLE {SCHEMA}.audit_event (
            seq           bigint       NOT NULL,
            occurred_at   timestamptz  NOT NULL,
            id            uuid         NOT NULL,
            actor_type    varchar(20)  NOT NULL,
            actor_id      varchar(64),
            actor_name    varchar(200) NOT NULL,
            action        varchar(100) NOT NULL,
            category      varchar(20)  NOT NULL,
            resource_type varchar(50),
            resource_id   varchar(100),
            resource_name varchar(200),
            outcome       varchar(20)  NOT NULL,
            changes       jsonb,
            context       jsonb        NOT NULL,
            reason        text,
            prev_hash     varchar(64)  NOT NULL,
            hash          varchar(64)  NOT NULL,
            CONSTRAINT pk_audit_event PRIMARY KEY (seq, occurred_at)
        ) PARTITION BY RANGE (occurred_at)
        """
    )
    for name, columns in {
        "ix_audit_event_occurred_at": "occurred_at",
        "ix_audit_event_id": "id",
        "ix_audit_event_actor_id": "actor_id",
        "ix_audit_event_resource": "resource_type, resource_id",
        "ix_audit_event_action": "action",
    }.items():
        op.execute(f"CREATE INDEX {name} ON {SCHEMA}.audit_event ({columns})")

    # Ningún UPDATE / DELETE / TRUNCATE, ni siquiera del dueño de la tabla.
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.audit_event_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'sx.audit_event es de solo inserción (operación % rechazada)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END
        $$
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_audit_event_immutable
        BEFORE UPDATE OR DELETE ON {SCHEMA}.audit_event
        FOR EACH ROW EXECUTE FUNCTION {SCHEMA}.audit_event_immutable()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_audit_event_no_truncate
        BEFORE TRUNCATE ON {SCHEMA}.audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.audit_event_immutable()
        """
    )

    # Particiones mensuales. El scheduler la invoca a diario para tener siempre meses por delante;
    # la partición DEFAULT solo recoge eventos si eso fallara durante meses.
    op.execute(
        f"""
        CREATE FUNCTION {SCHEMA}.audit_ensure_partitions(months_ahead integer DEFAULT 3)
        RETURNS integer
        LANGUAGE plpgsql
        SECURITY DEFINER
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
                part_name := 'audit_event_' || to_char(month_start, 'YYYYMM');
                IF to_regclass('{SCHEMA}.' || part_name) IS NULL THEN
                    IF EXISTS (
                        SELECT 1 FROM {SCHEMA}.audit_event_default
                        WHERE occurred_at >= month_start
                          AND occurred_at < month_start + interval '1 month'
                    ) THEN
                        RAISE WARNING 'Hay eventos de % en la partición DEFAULT; no se crea %',
                            month_start, part_name;
                        CONTINUE;
                    END IF;
                    EXECUTE format(
                        'CREATE TABLE {SCHEMA}.%I PARTITION OF {SCHEMA}.audit_event '
                        'FOR VALUES FROM (%L) TO (%L)',
                        part_name, month_start::timestamptz,
                        (month_start + interval '1 month')::timestamptz
                    );
                    EXECUTE format(
                        'CREATE TRIGGER trg_%s_no_truncate BEFORE TRUNCATE ON {SCHEMA}.%I '
                        'FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.audit_event_immutable()',
                        part_name, part_name
                    );
                    created := created + 1;
                END IF;
            END LOOP;
            RETURN created;
        END
        $$
        """
    )
    op.execute(
        f"CREATE TABLE {SCHEMA}.audit_event_default PARTITION OF {SCHEMA}.audit_event DEFAULT"
    )
    op.execute(
        f"""
        CREATE TRIGGER trg_audit_event_default_no_truncate
        BEFORE TRUNCATE ON {SCHEMA}.audit_event_default
        FOR EACH STATEMENT EXECUTE FUNCTION {SCHEMA}.audit_event_immutable()
        """
    )
    op.execute(f"SELECT {SCHEMA}.audit_ensure_partitions(3)")
    op.execute(f"REVOKE UPDATE, DELETE, TRUNCATE ON {SCHEMA}.audit_event FROM PUBLIC")


def downgrade() -> None:
    op.execute(f"DROP TABLE IF EXISTS {SCHEMA}.audit_event CASCADE")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.audit_ensure_partitions(integer)")
    op.execute(f"DROP FUNCTION IF EXISTS {SCHEMA}.audit_event_immutable()")
    op.execute(f"DROP SEQUENCE IF EXISTS {SCHEMA}.audit_event_seq")
    op.execute(
        f"DELETE FROM {SCHEMA}.system_setting "
        "WHERE key IN ('audit.last_verification', 'audit.chain_anchor')"
    )
