# Spike SAP

Script aislado para validar, contra el SAP real de AG, cómo se comportan los servicios OData ODP/CDS antes de construir el motor de extracción. Las hipótesis H1–H18 y el registro de resultados están en [ADR 0002](../../docs/adr/0002-extraccion-sap-odp-odata.md).

## Preparación en SAP (equipo SAP / Basis)

Publicar al menos tres servicios según [plan 05 §5](../../docs/plan/05-origenes-sap.md):

1. Un **extractor estándar con delta** (p. ej. de FI o SD) vía SEGW → *Redefine → ODP Extraction*, contexto SAPI.
2. Un **DataSource Z** expuesto para ODP (`RODPS_OS_EXPOSE`) y publicado igual.
3. Una **CDS** con `@Analytics.dataExtraction.enabled: true` y delta, contexto ABAP_CDS.

Además:

- Un usuario técnico de comunicación con `S_SERVICE` sobre esos servicios.
- Hacerlo en **DEV o QAS**, nunca en PRD: las pruebas abren suscripciones y H12 las cierra.
- Para H6, dos valores de filtro con datos y en los que se puedan generar cambios (p. ej. dos sociedades).
- Alguien del equipo SAP con acceso a ODQMON y a SAP GUI durante la sesión: varias hipótesis se cierran mirando ahí.
- Para H8, los límites que indique Basis: tiempo máximo de los procesos de diálogo y *timeouts* de ICM y Gateway.

## Sesiones recomendadas

Ejecute cada sesión una vez por servicio (extractor estándar, Z y CDS). Añada `--client <mandante>`; la contraseña se pide por teclado o se lee de `SPIKE_SAP_PASSWORD`, y no se guarda en ningún archivo.

```bash
BASE="--base-url https://<gateway>:<puerto> --client <mandante> --service <SERVICIO_SRV> --entity-set <EntityOf...|FactsOf...> --user <USUARIO_TECNICO>"
```

| Sesión | Hipótesis | Comando |
|---|---|---|
| 1 · Prioritaria | H6, H12, H18: deciden si dos pipelines pueden leer en delta el mismo origen | `uv run tools/spike-sap/spike_odp.py $BASE --hypotheses H6,H12,H18 --filter "RBUKRS eq '1000'" --filter-b "RBUKRS eq '2000'" --allow-terminate` |
| 2 · Núcleo | H1–H5, H11, H14–H17 | `uv run tools/spike-sap/spike_odp.py $BASE --currencies CLP,JPY` |
| 3 · Límites | H7, H8, H9, H10 | `uv run tools/spike-sap/spike_odp.py $BASE --hypotheses H7,H8,H9,H10 --dialog-limit-seconds <límite de Basis>` |
| 4 · Al día siguiente | H13 | `uv run tools/spike-sap/spike_odp.py $BASE --hypotheses H13 --previous-results tools/spike-sap/output/<fecha de la sesión 2>/results.json` |

- Sin `--allow-terminate`, H12 solo comprueba que las funciones existan; con él, el script pide escribir `CERRAR` antes de llamar a `TerminateDeltasFor…`.
- El script se detiene cuando necesita algo en SAP (crear, modificar o borrar registros; borrar la suscripción en ODQMON para H7) y continúa al pulsar Enter.
- H8 debe repetirse sin filtro sobre el extractor más grande disponible.
- Otras opciones: `--page-size 1000|5000|20000` (H5), `--mask-fields CAMPO1,CAMPO2` (enmascarar en muestras y listas), `--currency-field` (H14, si no se detecta), `--ca-bundle ca.pem`, `--hypotheses all`.

## Cómo leer el resultado

| Resultado | Significado |
|---|---|
| **se cumple** | El script lo comprobó solo |
| **no se cumple** | El script comprobó lo contrario; el detalle dice qué falló |
| **revisar a mano** | El script dejó la evidencia; falta la comprobación manual que lista `report.md` |
| **no ejecutada** | Faltaba un dato (p. ej. el delta llegó vacío o no se pasó `--filter-b`) |
| **error** | Fallo inesperado; ver `results.json` |

Resultados en `tools/spike-sap/output/<fecha>/` (ignorado por git):

| Archivo | Uso |
|---|---|
| `report.md` | Tabla de hipótesis, comprobaciones manuales y suscripciones abiertas: se pega en el ADR 0002 |
| `results.json` | Evidencia completa (tiempos, tokens, comparaciones, códigos de error). Lo usa H13 al día siguiente |
| `h14_importes.md`, `h15_fechas.md` | Listas para comparar con SAP GUI |
| `metadata.xml`, `*.sample.json` | Respuestas reales (pocas filas) para alimentar `tools/sap-mock` — **revisar y anonimizar antes de versionar** |

Al terminar cada sesión, borre en **ODQMON** las suscripciones que lista `report.md`.

## Después de la sesión

1. Pegar cada `report.md` en el ADR 0002 y pasar la columna "Resultado" de _pendiente_ al valor real.
2. Llevar al mock lo que SAP haga distinto: errores (código y mensaje de H7), funciones de H12, repetición de H11, contenido de los registros D de H17, tratamiento de `$top=0` de H9. La prueba `tools/sap-mock/tests/test_spike_against_mock.py` describe el comportamiento actual del mock y debe cambiar con él.

## Prueba en local contra el mock

```bash
docker compose up -d sap-mock
SPIKE_SAP_PASSWORD=sxmock uv run tools/spike-sap/spike_odp.py --base-url http://localhost:8081 --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user sxmock --non-interactive --hypotheses all --filter "RBUKRS eq '1000'" --filter-b "RBUKRS eq '2000'" --mock-admin-url http://localhost:8081/_mock/services/ZFI_WS_GL_ITEMS_SRV
```

Con `--mock-admin-url` el script genera los cambios y borra la suscripción a través del mock. Los resultados contra el mock reflejan el mock, no SAP. CI ejecuta todas las pruebas contra el mock (`cd tools/sap-mock && uv run pytest`).
