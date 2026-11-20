"""Errores del flujo de datos con su clase, que decide si se reintenta (plan 06 §8)."""

from __future__ import annotations

from enum import StrEnum


class ErrorKind(StrEnum):
    TRANSIENT = "transient"  # se reintenta como intento nuevo del run
    CONFIGURATION = "configuration"  # credenciales, permisos, esquema incompatible
    DATA = "data"  # conversión imposible, clave nula en upsert
    DELTA = "delta"  # token rechazado: el pipeline pasa a BROKEN


class DataflowError(Exception):
    """Error de un conector o destino. ``code`` es estable: SX-SAP-*, SX-DST-*, SX-SRC-*."""

    def __init__(self, message: str, *, kind: ErrorKind, code: str) -> None:
        super().__init__(message)
        self.message = message
        self.kind = kind
        self.code = code

    @property
    def retryable(self) -> bool:
        return self.kind is ErrorKind.TRANSIENT
