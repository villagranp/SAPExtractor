"""Catálogo de permisos (``recurso:acción``) y roles de sistema.

El catálogo vive en código y se sincroniza con la base de datos en cada ``db upgrade``.
Ver docs/plan/02-usuarios-autenticacion-roles.md §2.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PermissionDef:
    code: str
    module: str
    description: str


@dataclass(frozen=True)
class SystemRoleDef:
    code: str
    name: str
    description: str
    permissions: frozenset[str]


MODULES: dict[str, str] = {
    "identity": "Identidad",
    "audit": "Auditoría",
    "sap_connections": "Conexiones SAP",
    "sources": "Orígenes",
    "destinations": "Destinos",
    "pipelines": "Pipelines",
    "monitoring": "Monitoreo",
    "system": "Sistema",
}

PERMISSIONS: tuple[PermissionDef, ...] = (
    # Identidad
    PermissionDef("user:read", "identity", "Ver usuarios"),
    PermissionDef("user:create", "identity", "Crear usuarios"),
    PermissionDef("user:update", "identity", "Editar usuarios, roles asignados y bloqueos"),
    PermissionDef("user:disable", "identity", "Desactivar y reactivar usuarios"),
    PermissionDef("user:delete", "identity", "Anonimizar usuarios"),
    PermissionDef("role:read", "identity", "Ver roles y permisos"),
    PermissionDef("role:manage", "identity", "Crear, editar y eliminar roles"),
    PermissionDef("session:revoke", "identity", "Cerrar sesiones de otros usuarios"),
    PermissionDef("idp:manage", "identity", "Configurar Microsoft Entra ID"),
    PermissionDef("api_token:manage", "identity", "Gestionar tokens API"),
    # Auditoría
    PermissionDef("audit:read", "audit", "Consultar la auditoría"),
    PermissionDef("audit:export", "audit", "Exportar la auditoría y verificar su integridad"),
    PermissionDef("log:read", "audit", "Consultar logs de ejecución"),
    # Conexiones SAP
    PermissionDef("sap_connection:read", "sap_connections", "Ver conexiones SAP"),
    PermissionDef("sap_connection:manage", "sap_connections", "Gestionar conexiones SAP"),
    PermissionDef("sap_connection:test", "sap_connections", "Probar conexiones SAP"),
    # Orígenes
    PermissionDef("source:read", "sources", "Ver orígenes"),
    PermissionDef("source:manage", "sources", "Gestionar orígenes"),
    PermissionDef("source:preview", "sources", "Vista previa de datos de un origen"),
    # Destinos
    PermissionDef("destination:read", "destinations", "Ver destinos"),
    PermissionDef("destination:manage", "destinations", "Gestionar destinos"),
    PermissionDef("destination:test", "destinations", "Probar destinos"),
    # Pipelines
    PermissionDef("pipeline:read", "pipelines", "Ver pipelines y ejecuciones"),
    PermissionDef("pipeline:manage", "pipelines", "Gestionar pipelines y programación"),
    PermissionDef("pipeline:run", "pipelines", "Ejecutar y reintentar pipelines"),
    PermissionDef("pipeline:pause", "pipelines", "Pausar y reanudar pipelines"),
    PermissionDef("pipeline:reset_delta", "pipelines", "Reiniciar la suscripción de delta"),
    # Monitoreo
    PermissionDef("dashboard:read", "monitoring", "Ver el panel de monitoreo"),
    PermissionDef("alert:manage", "monitoring", "Gestionar reglas de alerta"),
    PermissionDef("alert:ack", "monitoring", "Reconocer y cerrar alertas"),
    # Sistema
    PermissionDef("settings:manage", "system", "Cambiar ajustes globales"),
    PermissionDef("secret:rotate", "system", "Rotar la clave maestra de cifrado"),
)

PERMISSION_CODES: frozenset[str] = frozenset(p.code for p in PERMISSIONS)

PLATFORM_ADMIN = "platform_admin"

_READ_DATA = frozenset(
    {
        "sap_connection:read",
        "source:read",
        "destination:read",
        "pipeline:read",
        "dashboard:read",
    }
)

SYSTEM_ROLES: tuple[SystemRoleDef, ...] = (
    SystemRoleDef(
        PLATFORM_ADMIN,
        "Administrador de plataforma",
        "Todo, incluida la identidad, Entra ID, secretos y ajustes globales.",
        PERMISSION_CODES,
    ),
    SystemRoleDef(
        "data_admin",
        "Administrador de datos",
        "Conexiones SAP, orígenes, destinos, pipelines y programación.",
        _READ_DATA
        | {
            "sap_connection:manage",
            "sap_connection:test",
            "source:manage",
            "source:preview",
            "destination:manage",
            "destination:test",
            "pipeline:manage",
            "pipeline:run",
            "pipeline:pause",
            "alert:manage",
            "alert:ack",
            "log:read",
        },
    ),
    SystemRoleDef(
        "operator",
        "Operador",
        "Ejecutar, pausar y reintentar pipelines; ver logs.",
        _READ_DATA | {"pipeline:run", "pipeline:pause", "log:read", "alert:ack"},
    ),
    SystemRoleDef(
        "viewer",
        "Visualizador",
        "Solo lectura de pipelines, ejecuciones y panel.",
        _READ_DATA,
    ),
    SystemRoleDef(
        "auditor",
        "Auditor",
        "Solo lectura de auditoría y logs; exportación.",
        frozenset(
            {"audit:read", "audit:export", "log:read", "user:read", "role:read", "dashboard:read"}
        ),
    ),
)

SYSTEM_ROLE_CODES: frozenset[str] = frozenset(r.code for r in SYSTEM_ROLES)

if not all(r.permissions <= PERMISSION_CODES for r in SYSTEM_ROLES):  # pragma: no cover
    msg = "Un rol de sistema usa un permiso que no está en el catálogo"
    raise RuntimeError(msg)
