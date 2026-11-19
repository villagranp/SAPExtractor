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
| H8 | Una init grande devuelve la primera página dentro de los límites de Gateway (tiempo máximo de proceso de diálogo, *timeout* de ICM). ¿Admite el servicio `Prefer: respond-async`? | Init del extractor más grande disponible; medir el tiempo hasta la primera página | _pendiente_ |
| H9 | Existe una init **sin datos** vía OData | Probar la init sin transferencia de datos y revisar ODQMON | _pendiente_ |
| H10 | Volver a pedir la misma página (`$skiptoken`) tras un corte devuelve lo mismo; volver a lanzar la init no crea otra suscripción | Cortar una init a medias, repetir la página y relanzar; revisar ODQMON | _pendiente_ |
| H11 | La repetición con el token anterior devuelve el mismo conjunto, o bien todo lo ocurrido desde ese token hasta ahora | Generar cambios entre las dos lecturas y comparar por claves y `ODQ_ENTITYCNTR`, no por recuento | _pendiente_ |
| H12 | `SubscribedTo<EntitySet>` y `TerminateDeltasFor<EntitySet>` existen en el `$metadata` de AG | Método HTTP, efecto en ODQMON y alcance: ¿cierra todas las suscripciones del entity set para el usuario técnico? | _pendiente_ |
| H13 | Retención de la ODQ: cuánto tiempo sigue siendo repetible un delta ya confirmado y qué pasa con una suscripción que deja de leer | Consultar el periodo de recuperación con Basis; repetir un delta al día siguiente | _pendiente_ |
| H14 | Importes en monedas sin decimales (CLP, JPY…): llegan en formato interno o externo | Comparar un documento con SAP GUI | _pendiente_ |
| H15 | `Edm.DateTime`: hora local del sistema o UTC, según el tipo ABAP del campo | Comparar campos de fecha y hora con SAP GUI | _pendiente_ |
| H16 | La vista previa con `$top` sobre un extractor grande no lanza la extracción completa | Medir duración y revisar ODQMON | _pendiente_ |
| H17 | Los registros con `ODQ_CHANGEMODE = D` solo traen informadas las claves | Borrar un registro en SAP y leer el delta | _pendiente_ |
| H18 | Dos inits sobre el mismo entity set, con los mismos filtros y el mismo usuario técnico, dejan dos cadenas de delta independientes | Dos inits seguidas, cambios en SAP y lectura de ambos deltas; revisar ODQMON. **Prioritaria**: un segundo destino es un segundo pipeline sobre el mismo origen | _pendiente_ |

H8–H18 se añadieron el 19.11.2026 (plan 13, C-01). El script del spike todavía no las cubre y hay que ampliarlo antes de ejecutar. Orden: primero H6, H12 y H18. El método de H2 se sustituye por el de H11: `same_row_count` da falso si entran cambios entre las dos lecturas aunque la repetición funcione.

## Cómo ejecutar el spike
Ver [`tools/spike-sap/README.md`](../../tools/spike-sap/README.md). Pegar aquí el `report.md` generado por cada servicio probado (un extractor estándar con delta, un Z y una CDS) y actualizar el estado del ADR.

## Consecuencias
- Toda fuente debe publicarse desde SAP (dependencia del equipo SAP; guía en plan 05 §5).
- Si H2 no se cumple, el motor debe conservar el spool hasta confirmar la carga y la repetición pasaría a depender de la ODQ (reinicialización) — se reflejaría en un ADR nuevo.
