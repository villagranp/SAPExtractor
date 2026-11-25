# Prueba en el SAP de AG (spike)

Guía de la sesión con el equipo de desarrollo SAP de AG (primera fecha: lunes 23.11.2026). El detalle técnico del script está en [`tools/spike-sap/README.md`](../../tools/spike-sap/README.md) y las hipótesis H1–H18 en el [ADR 0002](../adr/0002-extraccion-sap-odp-odata.md).

Comprobado el 22.11.2026: el script corre de punta a punta contra el mock actual (todas las hipótesis salvo H13, que necesita la ejecución del día anterior).

**Estado (24.11.2026).** La primera sesión se hizo en **DEV** el 23.11.2026 con `ZMM_WS_MAT_ODP_SRV` (0MATERIAL_ATTR): conexión, `$metadata`, H4 confirmada, H12 y H16 en parte, y una carga FULL de punta a punta hasta Snowflake con la plataforma. DEV tiene un solo material, así que **el resto se hace en QAS**, donde hay volumen y se pueden crear y modificar registros. Resultados y observaciones (TLS de HEC, red desde Docker) en el [ADR 0002](../adr/0002-extraccion-sap-odp-odata.md#sesión-1--dev-de-ag-23112026).

**Antes de la sesión en QAS:**

- Transportar a QAS el proyecto SEGW de `ZMM_WS_MAT_ODP_SRV` y activar el servicio en `/IWFND/MAINT_SERVICE`.
- Confirmar que los punteros de modificación de material están activos (BD61 y BD50).
- Elegir dos valores de filtro (p. ej. dos `MATKL` o dos rangos de `MATNR`) donde se puedan crear y modificar materiales durante la sesión (H6, H18).
- Pedir a Basis los límites de la sección 1, punto 7 (H8).
- Usuario: el mismo de DEV (aceptado por AG para DEV y QAS); PRD necesita usuario técnico.

**Reglas de la sesión**

- Solo en **DEV o QAS**, nunca en PRD: las pruebas abren suscripciones en la ODQ y H12 las cierra.
- Las contraseñas no se escriben en el chat, en archivos ni en la línea de comandos: el script las pide por teclado o las lee de `SPIKE_SAP_PASSWORD`.
- Las muestras que guarda el script (`*.sample.json`, `metadata.xml`) son datos de AG: no se versionan sin revisarlas y anonimizarlas, y se borran al terminar si AG lo pide.

---

## 1. Mensaje para el equipo SAP de AG (enviar antes de la sesión)

**Asunto:** Preparación de la prueba de extracción OData del lunes

Para la sesión del lunes necesitamos que esté listo lo siguiente en **DEV o QAS**:

| # | Qué | Para qué |
|---|---|---|
| 1 | Un **extractor estándar con delta** (p. ej. `0FI_GL_14` u otro de FI o SD) publicado vía SEGW → *Redefine → ODP Extraction*, contexto SAPI, y activado en `/IWFND/MAINT_SERVICE` | Prueba principal |
| 2 | Una **vista CDS** con `@Analytics.dataExtraction.enabled: true` y delta, publicada igual (contexto ABAP_CDS) | Segunda vía de extracción |
| 3 | Si existe, un **DataSource Z** expuesto para ODP (`RODPS_OS_EXPOSE`) y publicado igual | Caso Z (opcional el lunes) |
| 4 | Un **usuario técnico** de comunicación con `S_SERVICE` sobre esos servicios y autorización de lectura sobre los datos que extraen | Conexión del script |
| 5 | URL del Gateway (`https://host:puerto`), **mandante** y, si el certificado no es público, el **certificado de la CA** en PEM | Conexión |
| 6 | Dos valores de filtro con datos donde se puedan crear y modificar registros durante la sesión (p. ej. dos sociedades de prueba) | Hipótesis H6 y H18 |
| 7 | Límites de Basis: tiempo máximo de un proceso de diálogo (`rdisp/max_wprun_time`) y *timeouts* de ICM y Gateway | Hipótesis H8 |
| 8 | Durante la sesión, una persona con acceso a **ODQMON** y SAP GUI, que pueda crear, modificar y borrar registros de prueba | Varias hipótesis se cierran mirando ahí |

Y una comprobación de red rápida, idealmente antes del lunes: desde el equipo donde correrá la prueba, abrir en el navegador `https://<gateway>:<puerto>/sap/opu/odata/sap/<SERVICIO>_SRV/$metadata` con el usuario técnico. Si no carga, hay que abrir el acceso de red antes de la sesión.

---

## 2. Preparación del equipo (nuestra parte)

- `uv` instalado y el repositorio al día (`git pull`). El script solo depende de `httpx`; `uv` lo instala solo.
- VPN o red de AG funcionando y la comprobación de `$metadata` del punto anterior hecha.
- Para la parte opcional con la plataforma (sección 4): `docker compose up --build` funcionando y, **antes de dar de alta el SAP de AG**, una clave maestra propia en `SX_MASTER_KEY` (`cd backend && uv run sapextract generate-master-key`). La clave por defecto del compose de desarrollo es pública: no sirve para guardar credenciales reales.

---

## 3. Orden de la sesión

Una sesión por servicio, empezando por el extractor estándar. Variables comunes:

```bash
BASE="--base-url https://<gateway>:<puerto> --client <mandante> --service <SERVICIO_SRV> --entity-set <EntityOf...|FactsOf...> --user <USUARIO_TECNICO>"
```

| Orden | Sesión | Duración aprox. | Decide |
|---|---|---|---|
| 1 | **Prioritaria** (H6, H12, H18) con `--filter`, `--filter-b` y `--allow-terminate` | 45 min | Si dos pipelines pueden leer en delta el mismo origen y si las suscripciones se pueden cerrar desde la plataforma |
| 2 | **Núcleo** (H1–H5, H11, H14–H17) con `--currencies` de las monedas sin decimales que use AG | 60 min | Paginación, tokens, repetición, importes, fechas y registros borrados |
| 3 | **Límites** (H7–H10) con `--dialog-limit-seconds` | 45 min | Cargas iniciales grandes y qué pasa al borrar una suscripción |
| 4 | Repetir 1–3 sobre la **CDS** | 1–2 h | Diferencias entre extractor y CDS |
| 5 | **Al día siguiente:** H13 con `--previous-results` | 10 min | Retención de la ODQ (umbral de la alerta de suscripción estancada) |

Los comandos exactos de cada sesión están en el README del script. Al terminar cada sesión, el equipo de AG borra en **ODQMON** las suscripciones que lista `report.md`.

---

## 4. Opcional: la plataforma de punta a punta

Si sobra tiempo y AG está de acuerdo en que los datos de DEV o QAS pasen por el equipo de prueba, se valida lo construido en las iteraciones 2, 3 y 5 contra el SAP real:

1. **Sistemas SAP → Nuevo:** la prueba de conexión por pasos (red, DNS y TLS; autenticación en Gateway; servidor; latencia) y el explorador de servicios ODP/CDS.
2. **Orígenes → Nuevo:** origen sobre el extractor de la sesión 1, esquema y vista previa (sin suscripción).
3. **Destinos:** CSV en `/data/csv`.
4. **Pipelines:** pipeline delta, activar y «Ejecutar ahora» (init); provocar cambios en SAP y ejecutar de nuevo (delta).
5. Comparar las filas del CSV con SAP GUI y revisar el log del run.

Al terminar: borrar el pipeline (la suscripción queda pendiente de cerrar en ODQMON), el sistema SAP de la plataforma y los CSV generados.

---

## 5. Después de la sesión

1. Pegar cada `report.md` en el ADR 0002 y pasar la columna «Resultado» de _pendiente_ al valor real.
2. Ajustar las reglas provisionales del motor según lo observado (tabla «Opciones provisionales hasta el spike» del plan 06): H6/H18 (un solo pipeline delta por origen), H10 (init no reintentable), H12 (cierre manual), H7 (qué error es token rechazado), H13 (umbral de la alerta).
3. Llevar al mock lo que SAP haga distinto (errores de H7, funciones de H12, repetición de H11, registros D de H17, `$top=0` de H9) y actualizar `tools/sap-mock/tests/test_spike_against_mock.py`.
4. Escribir el ADR de cargas iniciales grandes con lo medido en H8–H10.
