# ADR 0002 · Extracción SAP vía ODP/OData

**Estado:** Propuesto — **pendiente de ejecutar el spike contra el SAP de AG** · **Fecha:** 18.11.2026

## Contexto
Hay que extraer extractores (DataSources estándar y Z) y vistas CDS con gestión de deltas. La API RFC de ODP (`RODPS_REPL_ODP_*`) no está permitida para clientes/terceros según la nota SAP 3255746; la vía soportada es OData.

## Decisión (propuesta)
- Extractores y CDS con delta se publican en SAP como servicios OData **ODP** (SEGW → *Redefine → ODP Extraction*; contextos SAPI y ABAP_CDS).
- La plataforma inicializa con `Prefer: odata.track-changes`, lee deltas con `!deltatoken`, y solo confirma el token tras cargar en destino (plan 06 §3).
- CDS publicadas sin ODP: lectura full o pseudo-delta por marca de agua.

## Hipótesis a validar en el spike

| # | Hipótesis | Cómo se valida | Resultado |
|---|---|---|---|
| H1 | La init con `odata.track-changes` devuelve `__delta` en la última página | `spike_odp.py` paso 3 | _pendiente_ |
| H2 | Re-solicitar con el token anterior repite el mismo delta (*repeat*) | Paso 6: `same_row_count` y `same_token` | _pendiente_ |
| H3 | Los registros delta traen `ODQ_CHANGEMODE` / `ODQ_ENTITYCNTR` | Paso 5 | _pendiente_ |
| H4 | La lectura full con `$top` no crea suscripción en ODQMON | Revisar ODQMON tras el paso 2 | _pendiente_ |
| H5 | Rendimiento aceptable (filas/seg) con `odata.maxpagesize` | Paso 3 con distintos `--page-size` | _pendiente_ |
| H6 | Comportamiento de dos suscripciones sobre el mismo servicio/usuario | Repetir init con otro filtro y revisar ODQMON | _pendiente_ |
| H7 | La suscripción puede cerrarse desde ODQMON y el token deja de valer | Borrar en ODQMON y reintentar delta | _pendiente_ |

## Cómo ejecutar el spike
Ver [`tools/spike-sap/README.md`](../../tools/spike-sap/README.md). Pegar aquí el `report.md` generado por cada servicio probado (un extractor estándar con delta, un Z y una CDS) y actualizar el estado del ADR.

## Consecuencias
- Toda fuente debe publicarse desde SAP (dependencia del equipo SAP; guía en plan 05 §5).
- Si H2 no se cumple, el motor debe conservar el spool hasta confirmar la carga y la repetición pasaría a depender de la ODQ (reinicialización) — se reflejaría en un ADR nuevo.
