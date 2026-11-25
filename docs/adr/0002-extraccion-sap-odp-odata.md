# ADR 0002 · Extracción SAP vía ODP/OData

**Estado:** Propuesto — **spike en curso**: primera sesión en DEV el 23.11.2026; el resto se hará en QAS (más datos y se pueden modificar) · **Fecha:** 18.11.2026

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
| H1 | La init con `odata.track-changes` devuelve `__delta` en la última página | Prueba núcleo: init completa | _pendiente (QAS)_ |
| H2 | Re-solicitar con el token anterior repite el mismo delta (*repeat*) | Prueba núcleo: la repetición contiene todos los registros del primer delta (por huella, no por recuento) | _pendiente (QAS)_ |
| H3 | Los registros delta traen `ODQ_CHANGEMODE` / `ODQ_ENTITYCNTR` | Prueba núcleo: todos los registros del delta los traen | _pendiente (QAS)_ |
| H4 | La lectura full con `$top` no crea suscripción en ODQMON | `SubscribedTo…` y `DeltaLinksOf…` antes y después de la vista previa; ODQMON | ✅ **Confirmada en DEV** (23.11.2026): tras la vista previa `DeltaLinksOf…` sigue en 0 y ODQMON y ROOSGEN siguen vacías. Extractor con 1 registro: repetir en QAS con volumen |
| H5 | Rendimiento aceptable (filas/seg) con `odata.maxpagesize` | Filas/s de la init; repetir con distintos `--page-size` | _pendiente (QAS)_ |
| H6 | Comportamiento de dos suscripciones sobre el mismo servicio/usuario | Inits con `--filter` y `--filter-b`; la repetición de B sigue disponible después de que A avance; ODQMON | _pendiente (QAS)_ |
| H7 | La suscripción puede cerrarse desde ODQMON y el token deja de valer | El script pide borrarla en ODQMON y reintenta el delta; registra el código de error de SAP | _pendiente (QAS)_ |
| H8 | Una init grande devuelve la primera página dentro de los límites de Gateway (tiempo máximo de proceso de diálogo, *timeout* de ICM). ¿Admite el servicio `Prefer: respond-async`? | Tiempo hasta la primera página frente a `--dialog-limit-seconds` (dato de Basis); petición con `respond-async` | _pendiente (QAS)_ |
| H9 | Existe una init **sin datos** vía OData | Init con `$top=0` (vía candidata, sin documentación de SAP que la respalde); ODQMON y equipo SAP | _pendiente (QAS)_ |
| H10 | Volver a pedir la misma página (`$skiptoken`) tras un corte devuelve lo mismo; volver a lanzar la init no crea otra suscripción | Corte tras la primera página, la segunda se pide dos veces y se relanza la init; ODQMON | _pendiente (QAS)_ |
| H11 | La repetición con el token anterior devuelve el mismo conjunto, o bien todo lo ocurrido desde ese token hasta ahora | Prueba núcleo: cambios entre la lectura y la repetición; comparación por huella (claves, `ODQ_CHANGEMODE`, `ODQ_ENTITYCNTR`, contenido). Registra qué token sigue valiendo después | _pendiente (QAS)_ |
| H12 | `SubscribedTo<EntitySet>` y `TerminateDeltasFor<EntitySet>` existen en el `$metadata` de AG | `FunctionImport` y método HTTP en el `$metadata`; con `--allow-terminate`, dos inits, cierre y comprobación de qué tokens siguen valiendo (alcance) | 🟡 **Parcial (DEV)**: las dos funciones existen, son `GET` y devuelven tipos complejos (`ChangeTrackingDetails.SubscribedFlag`, `SubscriptionTerminationDetails.ResultFlag`). Falta en QAS el cierre con `--allow-terminate` y su alcance |
| H13 | Retención de la ODQ: cuánto tiempo sigue siendo repetible un delta ya confirmado y qué pasa con una suscripción que deja de leer | `--previous-results` de una ejecución de hace ≥ 24 h: repite el delta confirmado y lee el último token; periodo de recuperación con Basis | _pendiente (QAS)_ |
| H14 | Importes en monedas sin decimales (CLP, JPY…): llegan en formato interno o externo | Registros en `--currencies`; cuenta importes con decimales no nulos y deja `h14_importes.md` para SAP GUI | _pendiente (QAS)_ |
| H15 | `Edm.DateTime`: hora local del sistema o UTC, según el tipo ABAP del campo | `h15_fechas.md` con valor bruto y leído sin zona, para SAP GUI | _pendiente (QAS)_ |
| H16 | La vista previa con `$top` sobre un extractor grande no lanza la extracción completa | Tiempo de `$top=1` y `$top=100`; ODQMON y SM50 | 🟡 **Parcial (DEV)**: `$top=1` 633 ms y `$top=100` 590 ms, sin extracción en ODQMON. DEV tiene 1 material, así que no prueba un extractor grande: repetir en QAS |
| H17 | Los registros con `ODQ_CHANGEMODE = D` solo traen informadas las claves | Prueba núcleo: campos no clave informados en los registros D | _pendiente (QAS)_ |
| H18 | Dos inits sobre el mismo entity set, con los mismos filtros y el mismo usuario técnico, dejan dos cadenas de delta independientes | Dos inits iguales; B recibe los mismos cambios que A y su repetición sigue disponible después de que A avance; ODQMON. **Prioritaria**: un segundo destino es un segundo pipeline sobre el mismo origen | _pendiente (QAS)_ |

H8–H18 se añadieron el 19.11.2026 (plan 13, C-01) y el script las cubre desde el 20.11.2026. Orden: primero H6, H12 y H18. El método de H2 se sustituyó por el de H11: comparar recuentos daba falso si entraban cambios entre las dos lecturas aunque la repetición funcionara.

## Sesión 1 · DEV de AG (23.11.2026)

**Entorno.** S/4HANA (`MATNR` de 40 caracteres), mandante 200, usuario de diálogo del consultor (aceptado por AG
para DEV y QAS; PRD necesita usuario técnico). Servicio `ZMM_WS_MAT_ODP_SRV`, entity set `EntityOf0MATERIAL_ATTR`
(0MATERIAL_ATTR, contexto SAPI): clave `MATNR`, 149 campos, `sap:change-tracking="true"`, trae `ODQ_CHANGEMODE` y
`ODQ_ENTITYCNTR`; filtrables `MATNR`, `MTART`, `MBRSH`, `MATKL` y `ATTYP`. DEV tiene un solo material.

**Ejecuciones.** `spike_odp.py --hypotheses H4,H16` (salida en `tools/spike-sap/output/20261123-165258`, no
versionada por llevar datos de AG) y, con la plataforma, un pipeline **FULL** de SAP DEV a Snowflake
(`S_0MATERIAL_ATTR_TO_SNF_DEV`): 1 fila extraída e insertada en unos 16 s, autenticación de Snowflake con PAT.
El modo FULL no envía `Prefer: odata.track-changes`; el camino delta (init, token, confirmación) **no** se ha
probado aún contra SAP.

**Observaciones que no son hipótesis.**

- *TLS*: el Gateway presenta el certificado comodín de SAP HEC (`*.us1.hec.sap.biz`, emitido por SAPNetCA_G2 →
  SAP Global Root CA), que no coincide con el nombre `*.sap.corporacionag.com`. En DEV se desactivó la
  verificación (permitido fuera de PRD). Para PRD hay que pedir a Basis un certificado con el nombre correcto en
  el SAN o usar un nombre que el certificado cubra, y cargar la CA de SAP en el sistema.
- *Autenticación*: el 401 anuncia `WWW-Authenticate: Negotiate`, pero Basic funciona.
- *`SubscribedTo…`*: SAP anida el indicador en el tipo complejo (`{"d": {"SubscribedTo…": {"SubscribedFlag": …}}}`);
  el script lo leía como nulo y se corrigió (commit `472799f`). El motor no llama aún a esta función.
- *Red*: con la VPN del equipo, los contenedores de Docker resuelven el nombre pero no llegan al Gateway por TCP;
  la prueba se hizo con la plataforma corriendo en el equipo. Para la instalación en AG, el servidor debe estar
  dentro de la red de AG o tener ruta al Gateway.

**Queda para QAS** (más datos y se pueden crear y modificar registros): la prueba núcleo (H1–H3, H11, H17), las
prioritarias (H6, H18 y el cierre de H12), los límites (H7–H10), el rendimiento con volumen (H5, H16), la
retención (H13, al día siguiente) y H14–H15 si el extractor tiene importes o fechas con hora. Hay que confirmar
antes que los punteros de modificación de material están activos (BD61/BD50). En 0MATERIAL_ATTR puede que nunca
aparezca un registro `D`: si es así, H17 se anota como «no observable con este extractor» y se prueba con otro.

## Cómo ejecutar el spike
Ver [`tools/spike-sap/README.md`](../../tools/spike-sap/README.md). Pegar aquí el `report.md` generado por cada servicio probado (un extractor estándar con delta, un Z y una CDS) y actualizar el estado del ADR.

## Consecuencias
- Toda fuente debe publicarse desde SAP (dependencia del equipo SAP; guía en plan 05 §5).
- Si H2 no se cumple, el motor debe conservar el spool hasta confirmar la carga y la repetición pasaría a depender de la ODQ (reinicialización) — se reflejaría en un ADR nuevo.
