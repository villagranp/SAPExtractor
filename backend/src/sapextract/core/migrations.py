"""Acceso programático a Alembic (migraciones dentro del paquete ``sapextract.migrations``)."""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

from sapextract.core.db import SCHEMA

SCRIPT_LOCATION = "sapextract:migrations"
VERSION_TABLE = "alembic_version"


def alembic_config(database_url: str | None = None) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", SCRIPT_LOCATION)
    if database_url:
        # '%' debe escaparse en la configuración de Alembic (ConfigParser).
        cfg.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return cfg


def head_revisions() -> set[str]:
    return set(ScriptDirectory.from_config(alembic_config()).get_heads())


def current_revisions(engine: Engine) -> set[str]:
    with engine.connect() as conn:
        ctx = MigrationContext.configure(
            conn, opts={"version_table_schema": SCHEMA, "version_table": VERSION_TABLE}
        )
        return set(ctx.get_current_heads())


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


def downgrade(database_url: str, revision: str) -> None:
    command.downgrade(alembic_config(database_url), revision)
