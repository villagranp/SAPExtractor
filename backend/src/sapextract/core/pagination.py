"""Paginación estándar de listados: ``?page=&page_size=&sort=``."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, Field

MAX_PAGE_SIZE = 200


class PageParams(BaseModel):
    page: int = Field(1, ge=1)
    page_size: int = Field(50, ge=1, le=MAX_PAGE_SIZE)
    sort: str | None = Field(
        None,
        description="Campo de orden; prefijo '-' para descendente (p. ej. '-created_at')",
        pattern=r"^-?[a-z_][a-z0-9_]*$",
    )

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    sort: Annotated[str | None, Query(pattern=r"^-?[a-z_][a-z0-9_]*$")] = None,
) -> PageParams:
    """Dependencia FastAPI para listados paginados."""
    return PageParams(page=page, page_size=page_size, sort=sort)


class Page[T](BaseModel):
    items: list[T]
    total: int
    page: int
    page_size: int

    @classmethod
    def of(cls, items: list[T], total: int, params: PageParams) -> Page[T]:
        return cls(items=items, total=total, page=params.page, page_size=params.page_size)
