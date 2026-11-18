"""Servicios OData simulados: definición de campos, generación de datos y registro de cambios."""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any

type Row = dict[str, Any]


class ServiceKind(StrEnum):
    ODP_SAPI = "ODP_SAPI"  # extractor (DataSource) expuesto vía SEGW ODP, contexto SAPI
    ODP_CDS = "ODP_CDS"  # CDS expuesta vía SEGW ODP, contexto ABAP_CDS
    CDS_ODATA = "CDS_ODATA"  # CDS publicada con @OData.publish (sin delta nativo)


@dataclass(frozen=True)
class FieldDef:
    name: str
    edm_type: str
    label: str
    key: bool = False
    max_length: int | None = None
    precision: int | None = None
    scale: int | None = None
    display_format: str | None = None  # "Date" para fechas SAP
    filterable: bool = True


ODQ_FIELDS = (
    FieldDef("ODQ_CHANGEMODE", "Edm.String", "Modo de cambio", max_length=1, filterable=False),
    FieldDef("ODQ_ENTITYCNTR", "Edm.Decimal", "Contador", precision=19, scale=0, filterable=False),
)


@dataclass
class Change:
    seq: int
    mode: str  # C, U, D
    row: Row
    counter: int  # 1 imagen posterior, -1 imagen anterior


@dataclass
class ServiceDef:
    technical_name: str
    description: str
    kind: ServiceKind
    odp_name: str
    entity_set: str
    entity_type: str
    fields: tuple[FieldDef, ...]
    make_row: Callable[[random.Random, int], Row]
    mutate_row: Callable[[random.Random, Row], Row]
    additive_delta: bool = False  # True: genera imagen anterior + posterior (estilo 2LIS)
    watermark_field: str | None = None

    @property
    def keys(self) -> tuple[str, ...]:
        return tuple(f.name for f in self.fields if f.key)

    @property
    def supports_delta(self) -> bool:
        return self.kind is not ServiceKind.CDS_ODATA

    @property
    def all_fields(self) -> tuple[FieldDef, ...]:
        return self.fields + ODQ_FIELDS if self.supports_delta else self.fields


@dataclass
class ServiceState:
    """Estado mutable de un servicio: filas actuales y registro de cambios (la 'ODQ')."""

    definition: ServiceDef
    rng: random.Random
    rows: dict[tuple[Any, ...], Row] = field(default_factory=dict)
    changes: list[Change] = field(default_factory=list)
    seq: int = 0
    issued_tokens: list[dict[str, Any]] = field(default_factory=list)
    invalidated_before: int = -1  # tokens con seq <= este valor se consideran borrados (ODQMON)

    def key_of(self, row: Row) -> tuple[Any, ...]:
        return tuple(row[k] for k in self.definition.keys)

    def seed(self, count: int) -> None:
        for i in range(count):
            row = self.definition.make_row(self.rng, i)
            self.rows[self.key_of(row)] = row

    def apply_changes(self, inserts: int, updates: int, deletes: int) -> int:
        d = self.definition
        start = len(self.rows) + len(self.changes) + 1_000_000
        for i in range(inserts):
            row = d.make_row(self.rng, start + i)
            self._touch(row)
            self.rows[self.key_of(row)] = row
            self._log("C", row, 1)
        for row in self.rng.sample(list(self.rows.values()), min(updates, len(self.rows))):
            if d.additive_delta:
                self._log("U", row, -1)  # imagen anterior
            new_row = d.mutate_row(self.rng, dict(row))
            self._touch(new_row)
            self.rows[self.key_of(new_row)] = new_row
            self._log("U", new_row, 1)
        for row in self.rng.sample(list(self.rows.values()), min(deletes, len(self.rows))):
            del self.rows[self.key_of(row)]
            self._log("D", row, -1 if d.additive_delta else 1)
        return self.seq

    def _touch(self, row: Row) -> None:
        if self.definition.watermark_field:
            row[self.definition.watermark_field] = datetime.now(UTC)

    def _log(self, mode: str, row: Row, counter: int) -> None:
        self.seq += 1
        self.changes.append(Change(self.seq, mode, dict(row), counter))

    def changes_between(self, after_seq: int, up_to_seq: int) -> list[Change]:
        return [c for c in self.changes if after_seq < c.seq <= up_to_seq]


# ---------------------------------------------------------------------- generadores


def _rand_date(rng: random.Random) -> date:
    return date(2026, 1, 1) + timedelta(days=rng.randint(0, 300))


def _gl_row(rng: random.Random, i: int) -> Row:
    return {
        "RBUKRS": rng.choice(["1000", "2000", "3000"]),
        "GJAHR": "2026",
        "BELNR": f"{100000000 + i:010d}",
        "BUZEI": f"{rng.randint(1, 3):03d}",
        "HKONT": f"{rng.choice([400000, 410000, 500000, 600000]) + rng.randint(0, 99):010d}",
        "BUDAT": _rand_date(rng),
        "WAERS": rng.choice(["USD", "EUR", "GTQ"]),
        "DMBTR": Decimal(rng.randint(100, 10_000_000)) / 100,
    }


def _gl_mutate(rng: random.Random, row: Row) -> Row:
    row["DMBTR"] = Decimal(rng.randint(100, 10_000_000)) / 100
    return row


def _sales_row(rng: random.Random, i: int) -> Row:
    qty = Decimal(rng.randint(1, 500))
    return {
        "VBELN": f"{5000000 + i // 3:010d}",
        "POSNR": f"{(i % 3 + 1) * 10:06d}",
        "MATNR": f"MAT-{rng.randint(1, 400):05d}",
        "KWMENG": qty,
        "NETWR": (qty * Decimal(rng.randint(100, 5000)) / 100).quantize(Decimal("0.01")),
        "WAERK": "USD",
        "ERDAT": _rand_date(rng),
    }


def _sales_mutate(rng: random.Random, row: Row) -> Row:
    row["KWMENG"] = Decimal(rng.randint(1, 500))
    row["NETWR"] = (row["KWMENG"] * Decimal(rng.randint(100, 5000)) / 100).quantize(Decimal("0.01"))
    return row


def _account_row(rng: random.Random, i: int) -> Row:
    return {
        "CompanyCode": rng.choice(["1000", "2000"]),
        "GLAccount": f"{400000 + i:010d}",
        "GLAccountName": f"Cuenta {400000 + i}",
        "IsBlocked": rng.random() < 0.05,
        "LastChangeDateTime": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
    }


def _account_mutate(rng: random.Random, row: Row) -> Row:
    row["GLAccountName"] = f"{row['GLAccountName'].split(' (')[0]} (rev {rng.randint(1, 99)})"
    row["IsBlocked"] = not row["IsBlocked"]
    return row


def _material_row(rng: random.Random, i: int) -> Row:
    return {
        "Material": f"MAT-{i + 1:05d}",
        "MaterialType": rng.choice(["FERT", "HALB", "ROH"]),
        "BaseUnit": rng.choice(["EA", "KG", "L"]),
        "GrossWeight": Decimal(rng.randint(1, 100_000)) / 1000,
        "CreatedOn": _rand_date(rng),
        "LastChangeDateTime": datetime(2026, 1, 1, tzinfo=UTC) + timedelta(minutes=i),
    }


def _material_mutate(rng: random.Random, row: Row) -> Row:
    row["GrossWeight"] = Decimal(rng.randint(1, 100_000)) / 1000
    return row


SERVICES: tuple[ServiceDef, ...] = (
    ServiceDef(
        technical_name="ZFI_WS_GL_ITEMS_SRV",
        description="ODP: partidas de mayor (0FI_GL_14)",
        kind=ServiceKind.ODP_SAPI,
        odp_name="0FI_GL_14",
        entity_set="EntityOf0FI_GL_14",
        entity_type="EntityOf0FI_GL_14Type",
        fields=(
            FieldDef("RBUKRS", "Edm.String", "Sociedad", key=True, max_length=4),
            FieldDef("GJAHR", "Edm.String", "Ejercicio", key=True, max_length=4),
            FieldDef("BELNR", "Edm.String", "Documento", key=True, max_length=10),
            FieldDef("BUZEI", "Edm.String", "Posición", key=True, max_length=3),
            FieldDef("HKONT", "Edm.String", "Cuenta de mayor", max_length=10),
            FieldDef("BUDAT", "Edm.DateTime", "Fecha contabilización", display_format="Date"),
            FieldDef("WAERS", "Edm.String", "Moneda", max_length=5),
            FieldDef("DMBTR", "Edm.Decimal", "Importe ML", precision=17, scale=2),
        ),
        make_row=_gl_row,
        mutate_row=_gl_mutate,
    ),
    ServiceDef(
        technical_name="ZSD_WS_SALES_ITEMS_SRV",
        description="ODP: posiciones de pedido de ventas (2LIS_11_VAITM, delta aditivo)",
        kind=ServiceKind.ODP_SAPI,
        odp_name="2LIS_11_VAITM",
        entity_set="EntityOf2LIS_11_VAITM",
        entity_type="EntityOf2LIS_11_VAITMType",
        fields=(
            FieldDef("VBELN", "Edm.String", "Documento de ventas", key=True, max_length=10),
            FieldDef("POSNR", "Edm.String", "Posición", key=True, max_length=6),
            FieldDef("MATNR", "Edm.String", "Material", max_length=40),
            FieldDef("KWMENG", "Edm.Decimal", "Cantidad", precision=15, scale=3),
            FieldDef("NETWR", "Edm.Decimal", "Valor neto", precision=15, scale=2),
            FieldDef("WAERK", "Edm.String", "Moneda", max_length=5),
            FieldDef("ERDAT", "Edm.DateTime", "Creado el", display_format="Date"),
        ),
        make_row=_sales_row,
        mutate_row=_sales_mutate,
        additive_delta=True,
    ),
    ServiceDef(
        technical_name="ZFI_WS_CV_GL_ACCOUNT_SRV",
        description="ODP: CDS de cuentas de mayor (ZFI_CV_GL_ACCOUNT, CDC)",
        kind=ServiceKind.ODP_CDS,
        odp_name="ZFI_CV_GL_ACCOUNT",
        entity_set="FactsOfZFI_CV_GL_ACCOUNT",
        entity_type="FactsOfZFI_CV_GL_ACCOUNTType",
        fields=(
            FieldDef("CompanyCode", "Edm.String", "Sociedad", key=True, max_length=4),
            FieldDef("GLAccount", "Edm.String", "Cuenta de mayor", key=True, max_length=10),
            FieldDef("GLAccountName", "Edm.String", "Denominación", max_length=50),
            FieldDef("IsBlocked", "Edm.Boolean", "Bloqueada"),
            FieldDef("LastChangeDateTime", "Edm.DateTimeOffset", "Última modificación"),
        ),
        make_row=_account_row,
        mutate_row=_account_mutate,
        watermark_field="LastChangeDateTime",
    ),
    ServiceDef(
        technical_name="ZMM_CV_MATERIAL_CDS",
        description="CDS publicada (@OData.publish): maestro de materiales",
        kind=ServiceKind.CDS_ODATA,
        odp_name="ZMM_CV_MATERIAL",
        entity_set="ZMM_CV_MATERIAL",
        entity_type="ZMM_CV_MATERIALType",
        fields=(
            FieldDef("Material", "Edm.String", "Material", key=True, max_length=40),
            FieldDef("MaterialType", "Edm.String", "Tipo de material", max_length=4),
            FieldDef("BaseUnit", "Edm.String", "Unidad base", max_length=3),
            FieldDef("GrossWeight", "Edm.Decimal", "Peso bruto", precision=13, scale=3),
            FieldDef("CreatedOn", "Edm.DateTime", "Creado el", display_format="Date"),
            FieldDef("LastChangeDateTime", "Edm.DateTime", "Última modificación"),
        ),
        make_row=_material_row,
        mutate_row=_material_mutate,
        watermark_field="LastChangeDateTime",
    ),
)
