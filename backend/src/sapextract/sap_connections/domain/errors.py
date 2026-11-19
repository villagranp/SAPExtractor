"""Errores de comunicación con SAP (códigos ``SX-SAP-*``).

Se responden como 502/504 (fallo del sistema remoto), nunca como 401/403: un 401 de SAP
no significa que la sesión del usuario en SAPExtract haya caducado.
"""

from __future__ import annotations

from typing import Any, ClassVar

from sapextract.core.exceptions import DomainError


class SapError(DomainError):
    code: ClassVar[str] = "SX-SAP-ERROR"
    status_code: ClassVar[int] = 502
    title: ClassVar[str] = "Error al comunicarse con SAP"

    def __init__(
        self,
        detail: str,
        *,
        http_status: int | None = None,
        sap_code: str | None = None,
        sap_message: str | None = None,
        transaction_id: str | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(detail)
        self.extra = extra or {}
        self.http_status = http_status
        self.sap_code = sap_code
        self.sap_message = sap_message
        self.transaction_id = transaction_id

    def as_details(self) -> dict[str, Any]:
        """Datos de diagnóstico sin secretos (para el informe de prueba y la auditoría)."""
        return self.extra | {
            k: v
            for k, v in {
                "http_status": self.http_status,
                "sap_code": self.sap_code,
                "sap_message": self.sap_message,
                "transaction_id": self.transaction_id,
            }.items()
            if v is not None
        }


class SapDnsError(SapError):
    code = "SX-SAP-DNS"
    title = "No se encontró el servidor SAP"


class SapNetworkError(SapError):
    code = "SX-SAP-NETWORK"
    title = "No se pudo conectar con SAP"


class SapProxyError(SapError):
    code = "SX-SAP-PROXY"
    title = "Error del proxy hacia SAP"


class SapTimeoutError(SapError):
    code = "SX-SAP-TIMEOUT"
    status_code = 504
    title = "SAP no respondió a tiempo"


class SapTlsError(SapError):
    code = "SX-SAP-TLS"
    title = "Error de TLS/certificado con SAP"


class SapAuthenticationError(SapError):
    code = "SX-SAP-AUTH"
    title = "SAP rechazó las credenciales"


class SapAuthorizationError(SapError):
    code = "SX-SAP-FORBIDDEN"
    title = "El usuario técnico no tiene autorización en SAP"


class SapOAuthError(SapError):
    code = "SX-SAP-OAUTH"
    title = "No se pudo obtener el token OAuth"


class SapNotFoundError(SapError):
    code = "SX-SAP-NOT-FOUND"
    title = "Recurso no encontrado en SAP"


class SapRateLimitError(SapError):
    code = "SX-SAP-RATE-LIMIT"
    title = "SAP limitó las peticiones"


class SapServerError(SapError):
    code = "SX-SAP-SERVER"
    title = "Error interno de SAP"


class SapRequestError(SapError):
    code = "SX-SAP-BAD-REQUEST"
    title = "SAP rechazó la petición"


class SapResponseError(SapError):
    code = "SX-SAP-INVALID-RESPONSE"
    title = "Respuesta de SAP no válida"


class SapBusyError(SapError):
    code = "SX-SAP-BUSY"
    status_code = 503
    title = "Límite de peticiones simultáneas alcanzado"
