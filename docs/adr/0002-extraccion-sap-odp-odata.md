# ADR 0002 · Extracción SAP vía ODP/OData

**Estado:** Propuesto — **pendiente de ejecutar el spike contra el SAP de AG** · **Fecha:** 18.11.2026

## Contexto
Hay que extraer extractores (DataSources estándar y Z) y vistas CDS con gestión de deltas. La API RFC de ODP (`RODPS_REPL_ODP_*`) no está permitida para clientes/terceros según la nota SAP 3255746; la vía soportada es OData.

## Decisión (propuesta)
- Extractores y CDS con delta se publican en SAP como servicios OData **ODP** (SEGW → *Redefine → ODP Extraction*; contextos SAPI y ABAP_CDS).
- La plataforma inicializa con `Prefer: odata.track-changes`, lee deltas con `!deltatoken`, y solo confirma el token tras cargar en destino (plan 06 §3).
- CDS publicadas sin ODP: lectura full o pseudo-delta por marca de agua.

## Hipótesis a validar en el spike

Cada hipótesis se ejecuta con `spike_odp.py --hypotheses Hn`. La columna "Cómo se valida" resume lo que hace el script; el `report.md` añade las comprobaciones manuales de cada una (ODQMON, SAP GUI, Basis).

| # | Hipótesis | Cómo se valida | Resultado |
|---|---|---|---|
| H1 | La init con `odata.track-changes` devuelve `__delta` en la última página | Prueba núcleo: init completa | _pendiente_ |
| H2 | Re-solicitar con el token anterior repite el mismo delta (*repeat*) | Prueba núcleo: la repetición contiene todos los registros del primer delta (por huella, no por recuento) | _pendiente_ |
| H3 | Los registros delta traen `ODQ_CHANGEMODE` / `ODQ_ENTITYCNTR` | Prueba núcleo: todos los registros del delta los traen | _pendiente_ |
| H4 | La lectura full con `$top` no crea suscripción en ODQMON | `SubscribedTo…` y `DeltaLinksOf…` antes y después de la vista previa; ODQMON | _pendiente_ |
| H5 | Rendimiento aceptable (filas/seg) con `odata.maxpagesize` | Filas/s de la init; repetir con distintos `--page-size` | _pendiente_ |
| H6 | Comportamiento de dos suscripciones sobre el mismo servicio/usuario | Inits con `--filter` y `--filter-b`; la repetición de B sigue disponible después de que A avance; ODQMON | _pendiente_ |
| H7 | La suscripción puede cerrarse desde ODQMON y el token deja de valer | El script pide borrarla en ODQMON y reintenta el delta; registra el código de error de SAP | _pendiente_ |
| H8 | Una init grande devuelve la primera página dentro de los límites de Gateway (tiempo máximo de proceso de diálogo, *timeout* de ICM). ¿Admite el servicio `Prefer: respond-async`? | Tiempo hasta la primera página frente a `--dialog-limit-seconds` (dato de Basis); petición con `respond-async` | _pendiente_ |
| H9 | Existe una init **sin datos** vía OData | Init con `$top=0` (vía candidata, sin documentación de SAP que la respalde); ODQMON y equipo SAP | _pendiente_ |
| H10 | Volver a pedir la misma página (`$skiptoken`) tras un corte devuelve lo mismo; volver a lanzar la init no crea otra suscripción | Corte tras la primera página, la segunda se pide dos veces y se relanza la init; ODQMON | _pendiente_ |
| H11 | La repetición con el token anterior devuelve el mismo conjunto, o bien todo lo ocurrido desde ese token hasta ahora | Prueba núcleo: cambios entre la lectura y la repetición; comparación por huella (claves, `ODQ_CHANGEMODE`, `ODQ_ENTITYCNTR`, contenido). Registra qué token sigue valiendo después | _pendiente_ |
| H12 | `SubscribedTo<EntitySet>` y `TerminateDeltasFor<EntitySet>` existen en el `$metadata` de AG | `FunctionImport` y método HTTP en el `$metadata`; con `--allow-terminate`, dos inits, cierre y comprobación de qué tokens siguen valiendo (alcance) | _pendiente_ |
| H13 | Retención de la ODQ: cuánto tiempo sigue siendo repetible un delta ya confirmado y qué pasa con una suscripción que deja de leer | `--previous-results` de una ejecución de hace ≥ 24 h: repite el delta confirmado y lee el último token; periodo de recuperación con Basis | _pendiente_ |
| H14 | Importes en monedas sin decimales (CLP, JPY…): llegan en formato interno o externo | Registros en `--currencies`; cuenta importes con decimales no nulos y deja `h14_importes.md` para SAP GUI | _pendiente_ |
| H15 | `Edm.DateTime`: hora local del sistema o UTC, según el tipo ABAP del campo | `h15_fechas.md` con valor bruto y leído sin zona, para SAP GUI | _pendiente_ |
| H16 | La vista previa con `$top` sobre un extractor grande no lanza la extracción completa | Tiempo de `$top=1` y `$top=100`; ODQMON y SM50 | _pendiente_ |
| H17 | Los registros con `ODQ_CHANGEMODE = D` solo traen informadas las claves | Prueba núcleo: campos no clave informados en los registros D | _pendiente_ |
| H18 | Dos inits sobre el mismo entity set, con los mismos filtros y el mismo usuario técnico, dejan dos cadenas de delta independientes | Dos inits iguales; B recibe los mismos cambios que A y su repetición sigue disponible después de que A avance; ODQMON. **Prioritaria**: un segundo destino es un segundo pipeline sobre el mismo origen | _pendiente_ |

H8–H18 se añadieron el 19.11.2026 (plan 13, C-01) y el script las cubre desde el 20.11.2026. Orden: primero H6, H12 y H18. El método de H2 se sustituyó por el de H11: comparar recuentos daba falso si entraban cambios entre las dos lecturas aunque la repetición funcionara.

## Cómo ejecutar el spike
Ver [`tools/spike-sap/README.md`](../../tools/spike-sap/README.md). Pegar aquí el `report.md` generado por cada servicio probado (un extractor estándar con delta, un Z y una CDS) y actualizar el estado del ADR.

## Consecuencias
- Toda fuente debe publicarse desde SAP (dependencia del equipo SAP; guía en plan 05 §5).
- Si H2 no se cumple, el motor debe conservar el spool hasta confirmar la carga y la repetición pasaría a depender de la ODQ (reinicialización) — se reflejaría en un ADR nuevo.
