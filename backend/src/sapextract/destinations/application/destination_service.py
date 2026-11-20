"""Casos de uso de destinos (plan 07): alta, edición, credenciales, prueba, estado, baja y DDL.

Mismo patrón que los sistemas SAP: los secretos solo se reciben y se guardan cifrados; la
prueba se ejecuta con la transacción cerrada y su resultado no cambia la versión; activar
exige una prueba correcta y vigente.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, SecretStr, ValidationError
from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session

from sapextract.audit.domain.diff import diff, snapshot
from sapextract.audit.domain.model import AuditEntry, Outcome, RequestInfo
from sapextract.core.clock import utcnow
from sapextract.core.exceptions import (
    BusinessRuleError,
    ConflictError,
    FieldIssue,
    NotFoundError,
)
from sapextract.core.pagination import PageParams
from sapextract.core.secret_store import SecretStore
from sapextract.dataflow.domain.ports import LoadOptions
from sapextract.dataflow.domain.schema import EntitySchema
from sapextract.destinations.application.plugins import DestinationPlugin
from sapextract.destinations.application.toolkit import DestinationDependent, DestinationsToolkit
from sapextract.destinations.domain.model import (
    DestinationEnvironment,
    DestinationStatus,
    DestinationType,
    check_load_options,
    normalize_code,
    resolve_columns,
)
from sapextract.destinations.domain.testing import CheckReport
from sapextract.destinations.infrastructure.models import Destination
from sapextract.identity.domain.model import Principal

# Tipo de secreto en SecretStore (no es una contraseña).
SECRET_KIND = "destination_credentials"  # noqa: S105  # nosec B105
SORTABLE = {"code", "name", "type", "environment", "status", "last_test_at", "created_at"}


@dataclass(frozen=True, kw_only=True)
class NewDestination:
    code: str
    name: str
    description: str | None
    type: DestinationType
    environment: DestinationEnvironment
    tags: list[str]
    config: dict[str, Any]
    secret: dict[str, Any] | None
    run_test: bool = True
    activate: bool = False


@dataclass(frozen=True, kw_only=True)
class DestinationChanges:
    version: int | None = None
    name: str | None = None
    description: str | None = None
    environment: DestinationEnvironment | None = None
    tags: list[str] | None = None
    config: dict[str, Any] | None = None


@dataclass(frozen=True, kw_only=True)
class DestinationFilters:
    q: str | None = None
    type: DestinationType | None = None
    environment: DestinationEnvironment | None = None
    status: DestinationStatus | None = None


@dataclass(frozen=True, kw_only=True)
class DestinationWithTest:
    destination: Destination
    report: CheckReport | None = None
    dependents: list[DestinationDependent] = field(default_factory=list)


@dataclass(frozen=True, kw_only=True)
class DdlPreview:
    statements: list[str]
    columns: list[dict[str, Any]]
    keys: list[str]


def _reveal(model: BaseModel) -> dict[str, Any]:
    """Valores reales del secreto (``SecretStr`` incluido) para cifrarlos."""
    return {
        k: (v.get_secret_value() if isinstance(v, SecretStr) else v)
        for k, v in model.__dict__.items()
    }


def _field_errors(exc: ValidationError, prefix: str) -> BusinessRuleError:
    issues = [
        FieldIssue(
            f"{prefix}." + ".".join(str(p) for p in err.get("loc", ())),
            str(err.get("msg", "")),
            "SX-DST-CONFIG-INVALID",
        )
        for err in exc.errors()
    ]
    msg = "Revise los campos marcados."
    return BusinessRuleError(msg, code="SX-DST-CONFIG-INVALID", errors=issues)


def _normalize_tags(tags: list[str]) -> list[str]:
    cleaned = sorted({t.strip().lower() for t in tags if t.strip()})
    if len(cleaned) > 20 or any(len(t) > 40 for t in cleaned):
        msg = "Hasta 20 etiquetas de 40 caracteres como máximo."
        raise BusinessRuleError(msg, code="SX-DST-TAGS-INVALID", errors=[FieldIssue("tags", msg)])
    return cleaned


class DestinationService:
    def __init__(self, session: Session, toolkit: DestinationsToolkit) -> None:
        self.session = session
        self.tk = toolkit
        self.secrets = SecretStore(session, toolkit.cipher)

    # ================================================================ consultas
    def search(
        self, filters: DestinationFilters, page: PageParams
    ) -> tuple[list[Destination], int]:
        stmt = select(Destination).where(Destination.deleted_at.is_(None))
        if filters.q:
            like = f"%{filters.q.strip()}%"
            stmt = stmt.where(
                or_(
                    Destination.code.ilike(like),
                    Destination.name.ilike(like),
                    Destination.description.ilike(like),
                )
            )
        if filters.type:
            stmt = stmt.where(Destination.type == filters.type.value)
        if filters.environment:
            stmt = stmt.where(Destination.environment == filters.environment.value)
        if filters.status:
            stmt = stmt.where(Destination.status == filters.status.value)
        total = self.session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        sort = page.sort or "code"
        name = sort.lstrip("-")
        column = getattr(Destination, name) if name in SORTABLE else Destination.code
        order = column.desc() if sort.startswith("-") else column.asc()
        items = list(
            self.session.scalars(
                stmt.order_by(order, Destination.code).offset(page.offset).limit(page.page_size)
            )
        )
        return items, total

    def get(self, destination_id: uuid.UUID) -> Destination:
        destination = self.session.get(Destination, destination_id)
        if destination is None or destination.deleted_at is not None:
            msg = "El destino no existe."
            raise NotFoundError(msg, code="SX-DST-NOT-FOUND")
        return destination

    def dependents(self, destination_id: uuid.UUID) -> list[DestinationDependent]:
        return self.tk.dependents_of(self.session, destination_id)

    def plugin(self, type_: DestinationType) -> DestinationPlugin:
        return self.tk.registry.get(type_)

    # ================================================================ configuración
    def _config(self, plugin: DestinationPlugin, data: dict[str, Any]) -> BaseModel:
        try:
            config = plugin.config_model.model_validate(data)
        except ValidationError as exc:
            raise _field_errors(exc, "config") from exc
        try:
            plugin.validate(config, self.tk.context)
        except BusinessRuleError as exc:
            exc.errors = [
                issue
                if issue.field.startswith("config.")
                else FieldIssue(f"config.{issue.field}", issue.message, issue.code)
                for issue in exc.errors
            ]
            raise
        return config

    def _secret(self, plugin: DestinationPlugin, data: dict[str, Any] | None) -> BaseModel | None:
        if plugin.secret_model is None:
            return None
        if data is None:
            msg = "Indique las credenciales del destino."
            raise BusinessRuleError(
                msg, code="SX-DST-SECRET-MISSING", errors=[FieldIssue("secret", msg)]
            )
        try:
            return plugin.secret_model.model_validate(data)
        except ValidationError as exc:
            raise _field_errors(exc, "secret") from exc

    def stored_secret(self, destination: Destination) -> BaseModel | None:
        plugin = self.plugin(DestinationType(destination.type))
        if plugin.secret_model is None or destination.secret_id is None:
            return None
        return plugin.secret_model.model_validate(self.secrets.read(destination.secret_id))

    def stored_config(self, destination: Destination) -> BaseModel:
        plugin = self.plugin(DestinationType(destination.type))
        return plugin.config_model.model_validate(destination.config)

    # ================================================================ alta y edición
    def create(
        self, data: NewDestination, actor: Principal, request: RequestInfo
    ) -> DestinationWithTest:
        code = normalize_code(data.code)
        self._ensure_code_free(code)
        plugin = self.plugin(data.type)
        config = self._config(plugin, data.config)
        secret = self._secret(plugin, data.secret)
        secret_row = (
            self.secrets.create(SECRET_KIND, _reveal(secret), actor=actor.username)
            if secret is not None
            else None
        )
        destination = Destination(
            code=code,
            name=data.name.strip(),
            description=(data.description or "").strip() or None,
            type=data.type.value,
            environment=data.environment.value,
            config=config.model_dump(mode="json"),
            secret_id=secret_row.id if secret_row else None,
            credential_info=plugin.credential_info(secret),
            tags=_normalize_tags(data.tags),
            status=DestinationStatus.DRAFT.value,
            created_by=actor.username,
            updated_by=actor.username,
        )
        self.session.add(destination)
        self.session.flush()
        self._audit(
            "destination.created", destination, actor, request, diff(None, _snapshot(destination))
        )
        self.session.commit()
        report = None
        if data.run_test or data.activate:
            report = self._test_and_store(destination, config, secret, actor, request)
            if data.activate and report.ok:
                self.activate(destination.id, actor, request)
        return DestinationWithTest(destination=self.get(destination.id), report=report)

    def update(
        self,
        destination_id: uuid.UUID,
        changes: DestinationChanges,
        actor: Principal,
        request: RequestInfo,
    ) -> Destination:
        destination = self.get(destination_id)
        if changes.version is not None and changes.version != destination.version:
            msg = "El destino fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        before = _snapshot(destination)
        if changes.name is not None:
            destination.name = changes.name.strip()
        if changes.description is not None:
            destination.description = changes.description.strip() or None
        if changes.environment is not None:
            destination.environment = changes.environment.value
        if changes.tags is not None:
            destination.tags = _normalize_tags(changes.tags)
        config_changed = False
        if changes.config is not None:
            plugin = self.plugin(DestinationType(destination.type))
            new_config = self._config(plugin, changes.config).model_dump(mode="json")
            config_changed = new_config != destination.config
            destination.config = new_config
        delta = diff(before, _snapshot(destination))
        if not delta:
            return destination
        if config_changed and destination.last_test_at is not None:
            destination.test_outdated = True
        destination.updated_by = actor.username
        destination.updated_at = utcnow()
        self.session.flush()
        self._audit("destination.updated", destination, actor, request, delta)
        self.session.commit()
        return destination

    def rotate_credentials(
        self,
        destination_id: uuid.UUID,
        secret_data: dict[str, Any],
        actor: Principal,
        request: RequestInfo,
        *,
        version: int | None = None,
    ) -> DestinationWithTest:
        destination = self.get(destination_id)
        if version is not None and version != destination.version:
            msg = "El destino fue modificado por otra persona; recargue y vuelva a intentarlo."
            raise ConflictError(msg, code="SX-GEN-STALE")
        plugin = self.plugin(DestinationType(destination.type))
        if plugin.secret_model is None:
            msg = "Este tipo de destino no usa credenciales."
            raise BusinessRuleError(msg, code="SX-DST-NO-SECRET")
        secret = self._secret(plugin, secret_data)
        if secret is None:  # pragma: no cover - _secret lanza antes si falta
            msg = "Indique las credenciales del destino."
            raise BusinessRuleError(msg, code="SX-DST-SECRET-MISSING")
        before = {"auth_identity": destination.credential_info}
        if destination.secret_id is None:
            destination.secret_id = self.secrets.create(
                SECRET_KIND, _reveal(secret), actor=actor.username
            ).id
        else:
            self.secrets.replace(destination.secret_id, _reveal(secret))
        destination.credential_info = plugin.credential_info(secret)
        destination.test_outdated = destination.last_test_at is not None
        destination.updated_by = actor.username
        destination.updated_at = utcnow()
        self.session.flush()
        changes = diff(before, {"auth_identity": destination.credential_info})
        changes["credentials"] = {"before": "***", "after": "***"}
        self._audit("destination.credentials_rotated", destination, actor, request, changes)
        self.session.commit()
        config = self.stored_config(destination)
        report = self._test_and_store(destination, config, secret, actor, request)
        return DestinationWithTest(destination=self.get(destination.id), report=report)

    # ================================================================ pruebas
    def test(
        self, destination_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> DestinationWithTest:
        destination = self.get(destination_id)
        report = self._test_and_store(
            destination,
            self.stored_config(destination),
            self.stored_secret(destination),
            actor,
            request,
        )
        return DestinationWithTest(destination=self.get(destination.id), report=report)

    def test_unsaved(
        self,
        type_: DestinationType,
        *,
        code: str | None,
        config_data: dict[str, Any],
        secret_data: dict[str, Any] | None,
        actor: Principal,
        request: RequestInfo,
        reuse_from: uuid.UUID | None = None,
    ) -> CheckReport:
        plugin = self.plugin(type_)
        config = self._config(plugin, config_data)
        existing = self.get(reuse_from) if reuse_from else None
        if existing is not None and existing.type != type_.value:
            msg = "El destino de referencia es de otro tipo."
            raise BusinessRuleError(msg, code="SX-DST-TYPE-MISMATCH")
        if secret_data is None and existing is not None:
            secret = self.stored_secret(existing)
        else:
            secret = self._secret(plugin, secret_data)
        self.session.commit()  # no mantener la transacción abierta durante la prueba
        report = plugin.test(config, secret, self.tk.context)
        label = code.strip().upper() if code and code.strip() else "borrador"
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="destination.tested",
                outcome=Outcome.SUCCESS if report.ok else Outcome.FAILURE,
                resource_type="destination",
                resource_id=str(existing.id) if existing else None,
                resource_name=existing.code if existing else label,
                details={"unsaved": True, "type": type_.value, **report.audit_details()},
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        return report

    def _test_and_store(
        self,
        destination: Destination,
        config: BaseModel,
        secret: BaseModel | None,
        actor: Principal,
        request: RequestInfo,
    ) -> CheckReport:
        plugin = self.plugin(DestinationType(destination.type))
        self.session.commit()
        report = plugin.test(config, secret, self.tk.context)
        self.session.execute(
            update(Destination)
            .where(Destination.id == destination.id)
            .values(
                last_test_at=report.finished_at,
                last_test_ok=report.ok,
                last_test_result=report.as_dict(),
                test_outdated=False,
            )
            .execution_options(synchronize_session=False)
        )
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action="destination.tested",
                outcome=Outcome.SUCCESS if report.ok else Outcome.FAILURE,
                resource_type="destination",
                resource_id=str(destination.id),
                resource_name=destination.code,
                details=report.audit_details(),
            ),
            actor=actor.actor,
            request=request,
        )
        self.session.commit()
        self.session.expire(destination)
        return report

    # ================================================================ estado y baja
    def activate(
        self, destination_id: uuid.UUID, actor: Principal, request: RequestInfo
    ) -> Destination:
        destination = self.get(destination_id)
        if destination.status == DestinationStatus.ACTIVE:
            return destination
        if not destination.last_test_ok or destination.test_outdated:
            msg = (
                "Pruebe el destino antes de activarlo: se exige una prueba correcta "
                "con la configuración y credenciales actuales."
            )
            raise BusinessRuleError(msg, code="SX-DST-TEST-REQUIRED")
        return self._set_status(
            destination,
            status=DestinationStatus.ACTIVE,
            reason=None,
            action="destination.activated",
            actor=actor,
            request=request,
        )

    def deactivate(
        self,
        destination_id: uuid.UUID,
        reason: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> DestinationWithTest:
        destination = self.get(destination_id)
        if destination.status != DestinationStatus.INACTIVE:
            self._set_status(
                destination,
                status=DestinationStatus.INACTIVE,
                reason=reason,
                action="destination.deactivated",
                actor=actor,
                request=request,
            )
        return DestinationWithTest(
            destination=destination, dependents=self.dependents(destination.id)
        )

    def delete(
        self,
        destination_id: uuid.UUID,
        reason: str | None,
        actor: Principal,
        request: RequestInfo,
    ) -> None:
        destination = self.get(destination_id)
        blocking = [d for d in self.dependents(destination.id) if d.active]
        if blocking:
            names = ", ".join(f"{d.kind} «{d.name}»" for d in blocking[:5])
            msg = f"No se puede eliminar: lo usan objetos activos ({names})."
            raise ConflictError(msg, code="SX-DST-IN-USE")
        self._audit(
            "destination.deleted",
            destination,
            actor,
            request,
            diff(_snapshot(destination), None),
            reason=reason,
        )
        secret_id = destination.secret_id
        destination.secret_id = None
        destination.deleted_at = utcnow()
        destination.status = DestinationStatus.INACTIVE.value
        destination.updated_by = actor.username
        self.session.flush()
        if secret_id is not None:
            self.secrets.delete(secret_id)
        self.session.commit()

    # ================================================================ DDL
    def ddl_preview(
        self, destination_id: uuid.UUID, schema: EntitySchema, options: LoadOptions
    ) -> DdlPreview:
        destination = self.get(destination_id)
        plugin = self.plugin(DestinationType(destination.type))
        check_load_options(options, plugin.supported_modes)
        statements = plugin.ddl(self.stored_config(destination), schema, options)
        names = resolve_columns(schema, options.column_mapping)
        columns = [
            {
                "source": c.name,
                "target": names.get(c.name),
                "edm_type": c.edm_type,
                "arrow_type": str(c.arrow_type),
                "is_key": c.is_key,
                "label": c.label,
            }
            for c in schema.columns
        ]
        return DdlPreview(statements=statements, columns=columns, keys=list(schema.keys))

    # ================================================================ utilidades
    def _set_status(
        self,
        destination: Destination,
        *,
        status: DestinationStatus,
        reason: str | None,
        action: str,
        actor: Principal,
        request: RequestInfo,
    ) -> Destination:
        before = destination.status
        destination.status = status.value
        destination.status_reason = (reason or "").strip() or None
        destination.updated_by = actor.username
        destination.updated_at = utcnow()
        self.session.flush()
        self._audit(
            action,
            destination,
            actor,
            request,
            {"status": {"before": before, "after": status.value}},
            reason=destination.status_reason,
        )
        self.session.commit()
        return destination

    def _ensure_code_free(self, code: str) -> None:
        exists = self.session.scalar(
            select(Destination.id).where(Destination.code == code, Destination.deleted_at.is_(None))
        )
        if exists is not None:
            msg = f"Ya existe un destino con el código «{code}»."
            raise ConflictError(msg, code="SX-DST-CODE-TAKEN")

    def _audit(
        self,
        action: str,
        destination: Destination,
        actor: Principal,
        request: RequestInfo,
        changes: dict[str, Any],
        *,
        reason: str | None = None,
    ) -> None:
        self.tk.audit.record(
            self.session,
            AuditEntry(
                action=action,
                resource_type="destination",
                resource_id=str(destination.id),
                resource_name=destination.code,
                changes=changes or None,
                reason=reason,
            ),
            actor=actor.actor,
            request=request,
        )


def _snapshot(destination: Destination) -> dict[str, Any]:
    return snapshot(
        {
            "code": destination.code,
            "name": destination.name,
            "description": destination.description,
            "type": destination.type,
            "environment": destination.environment,
            "config": destination.config,
            "auth_identity": destination.credential_info,
            "tags": list(destination.tags),
            "status": destination.status,
        }
    )
