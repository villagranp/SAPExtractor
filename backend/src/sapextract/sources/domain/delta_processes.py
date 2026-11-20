"""Semántica del delta a partir del proceso de delta del DataSource (RSA2, plan 05 §5.1).

El ``$metadata`` no informa el proceso de delta, así que la plataforma no lo detecta: el
usuario lo consulta en RSA2 y aquí se traduce a cómo debe cargarse el destino (plan 06 §3.4).
Los extractores conocidos son una **sugerencia** que siempre hay que confirmar en RSA2.
"""

from __future__ import annotations

from dataclasses import dataclass

from sapextract.sources.domain.model import DeltaSemantics

# Código del proceso de delta (campo DELTA de ROOSOURCE, texto en RSA2) → semántica.
DELTA_PROCESS_SEMANTICS: dict[str, DeltaSemantics] = {
    "ABR": DeltaSemantics.BEFORE_AFTER,  # imagen anterior, posterior y de anulación
    "ABR1": DeltaSemantics.BEFORE_AFTER,  # como ABR, serializado por petición
    "ADD": DeltaSemantics.ADDITIVE,  # solo imágenes aditivas (extractor)
    "ADDD": DeltaSemantics.ADDITIVE,  # solo imágenes aditivas (cola de delta)
    "AIE": DeltaSemantics.AFTER_IMAGE,  # imágenes posteriores (extractor)
    "AIED": DeltaSemantics.AFTER_IMAGE,  # imágenes posteriores con marca de borrado (extractor)
    "AIM": DeltaSemantics.AFTER_IMAGE,  # imágenes posteriores (cola de delta)
    "AIMD": DeltaSemantics.AFTER_IMAGE,  # imágenes posteriores con marca de borrado (cola)
    "NEWE": DeltaSemantics.AFTER_IMAGE,  # solo el estado nuevo de los registros modificados
}


@dataclass(frozen=True, kw_only=True)
class SemanticsHint:
    delta_process: str
    semantics: DeltaSemantics


# Extractores estándar frecuentes. Sugerencia: el proceso real se confirma en RSA2.
KNOWN_EXTRACTORS: dict[str, str] = {
    "0FI_GL_4": "AIE",
    "0FI_AR_4": "AIE",
    "0FI_AP_4": "AIE",
    "0FI_GL_14": "AIMD",
    "2LIS_02_HDR": "ABR",
    "2LIS_02_ITM": "ABR",
    "2LIS_11_VAHDR": "ABR",
    "2LIS_11_VAITM": "ABR",
    "2LIS_12_VCITM": "ABR",
    "2LIS_13_VDITM": "ABR",
}


def semantics_for_process(code: str) -> DeltaSemantics:
    return DELTA_PROCESS_SEMANTICS.get(code.strip().upper(), DeltaSemantics.UNKNOWN)


def hint_for(odp_name: str | None) -> SemanticsHint | None:
    process = KNOWN_EXTRACTORS.get((odp_name or "").upper())
    if process is None:
        return None
    return SemanticsHint(delta_process=process, semantics=semantics_for_process(process))
