"""Entorno de Alembic. La URL sale de ``SX_DATABASE_URL`` salvo que se pase explícitamente."""

from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool, text

from sapextract.core.db import SCHEMA
from sapextract.core.migrations import VERSION_TABLE
from sapextract.models import Base

config = context.config
target_metadata = Base.metadata


def _database_url() -> str:
    url = config.get_main_option("sqlalchemy.url")
    if url:
        return url
    from sapextract.core.config import get_settings  # noqa: PLC0415

    return get_settings().database_url


def _include_name(name: str | None, type_: str, _parent: object) -> bool:
    # Solo se gestionan objetos del esquema de la aplicación.
    if type_ == "schema":
        return name == SCHEMA
    return True


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        version_table=VERSION_TABLE,
        version_table_schema=SCHEMA,
        include_schemas=True,
        include_name=_include_name,
    )
    with context.begin_transaction():
        context.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}"))
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table=VERSION_TABLE,
            version_table_schema=SCHEMA,
            include_schemas=True,
            include_name=_include_name,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
