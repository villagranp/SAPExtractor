"""Acceso a la base de metadatos: engine, sesiones, Unit of Work y modelo base."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from types import TracebackType
from typing import Self

from sqlalchemy import DateTime, Engine, MetaData, String, create_engine, func, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from sapextract.core.config import Settings

SCHEMA = "sx"

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Modelo base: todas las tablas viven en el esquema ``sx``."""

    metadata = MetaData(schema=SCHEMA, naming_convention=NAMING_CONVENTION)


def new_id() -> uuid.UUID:
    """UUID v7: único y ordenable por tiempo (mejor localidad en índices)."""
    return uuid.uuid7()


class IdMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AuthorMixin:
    created_by: Mapped[str | None] = mapped_column(String(100))
    updated_by: Mapped[str | None] = mapped_column(String(100))


class Database:
    """Agrupa el engine y la fábrica de sesiones de un proceso."""

    def __init__(self, settings: Settings) -> None:
        self.engine: Engine = create_engine(
            settings.database_url,
            pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow,
            pool_pre_ping=True,
            echo=settings.db_echo,
        )
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        """Sesión con rollback automático si hay excepción."""
        session = self.session_factory()
        try:
            yield session
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def ping(self) -> None:
        with self.engine.connect() as conn:
            conn.execute(text("SELECT 1"))

    def dispose(self) -> None:
        self.engine.dispose()


class UnitOfWork:
    """Transacción explícita: los cambios solo se guardan con ``commit()``.

    Uso::

        with UnitOfWork(db) as uow:
            uow.session.add(entidad)
            uow.commit()
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self.session: Session

    def __enter__(self) -> Self:
        self.session = self._db.session_factory()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            self.session.rollback()
        self.session.close()

    def commit(self) -> None:
        self.session.commit()

    def rollback(self) -> None:
        self.session.rollback()
