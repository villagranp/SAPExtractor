"""Registro de plugins de destino (plan 07): cada tipo aporta su formulario, prueba y escritor.

Añadir un destino nuevo (SQL Server, ADLS…) es añadir un plugin aquí; el motor y la API no
cambian. La configuración no sensible se valida con un modelo Pydantic por plugin y el
frontend pinta el formulario a partir de su JSON Schema.
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Protocol

from pydantic import BaseModel

from sapextract.core.network import HostPolicy
from sapextract.dataflow.domain.batch import LoadMode
from sapextract.dataflow.domain.ports import DestinationWriter, LoadOptions
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.destinations.domain.model import CsvRoots, DestinationType
from sapextract.destinations.domain.testing import CheckReport


@dataclass(frozen=True, kw_only=True)
class PluginContext:
    host_policy: HostPolicy
    csv_roots: CsvRoots
    work_dir: Path  # carpeta temporal del proceso (p. ej. para claves o archivos de prueba)


def wrong_model(value: object, expected: type[BaseModel]) -> TypeError:
    """Error de programación: el registro entregó a un plugin un modelo que no es el suyo."""
    return TypeError(f"Se esperaba {expected.__name__}, se recibió {type(value).__name__}")


class DestinationPlugin(Protocol):
    kind: ClassVar[DestinationType]
    label: ClassVar[str]
    config_model: ClassVar[type[BaseModel]]
    secret_model: ClassVar[type[BaseModel] | None]
    supported_modes: ClassVar[frozenset[LoadMode]]

    def validate(self, config: BaseModel, ctx: PluginContext) -> None:
        """Reglas del entorno (hosts permitidos, raíces CSV). Lanza BusinessRuleError."""
        ...

    def credential_info(self, secret: BaseModel | None) -> dict[str, Any]:
        """Datos visibles de la credencial (nunca el secreto)."""
        ...

    def test(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> CheckReport: ...

    def open_writer(
        self, config: BaseModel, secret: BaseModel | None, ctx: PluginContext
    ) -> AbstractContextManager[DestinationWriter]: ...

    def ddl(self, config: BaseModel, schema: EntitySchema, options: LoadOptions) -> list[str]:
        """Sentencias que crearían o adaptarían el destino para ese esquema (vista previa)."""
        ...

    def config_json_schema(self, ctx: PluginContext) -> dict[str, Any]: ...


class PluginRegistry:
    def __init__(self, plugins: list[DestinationPlugin]) -> None:
        self._plugins = {p.kind: p for p in plugins}

    def get(self, type_: DestinationType) -> DestinationPlugin:
        return self._plugins[type_]

    def all(self) -> list[DestinationPlugin]:
        return list(self._plugins.values())
