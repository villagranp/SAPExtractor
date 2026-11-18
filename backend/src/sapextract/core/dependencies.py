"""Dependencias FastAPI compartidas."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from sapextract.core.config import Settings
from sapextract.core.db import Database


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_database(request: Request) -> Database:
    db: Database = request.app.state.db
    return db


def get_session(db: Annotated[Database, Depends(get_database)]) -> Iterator[Session]:
    with db.session() as session:
        yield session


SettingsDep = Annotated[Settings, Depends(get_app_settings)]
DatabaseDep = Annotated[Database, Depends(get_database)]
SessionDep = Annotated[Session, Depends(get_session)]
