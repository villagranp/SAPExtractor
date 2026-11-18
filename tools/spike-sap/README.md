# Spike SAP (iteración 0)

Script aislado para validar, contra el SAP real de AG, cómo se comportan los servicios OData ODP/CDS antes de construir el motor de extracción. Las hipótesis y el registro de resultados están en [ADR 0002](../../docs/adr/0002-extraccion-sap-odp-odata.md).

## Preparación en SAP (equipo SAP / Basis)

Publicar al menos tres servicios según [plan 05 §5](../../docs/plan/05-origenes-sap.md):

1. Un **extractor estándar con delta** (p. ej. de FI o SD) vía SEGW → *Redefine → ODP Extraction*, contexto SAPI.
2. Un **DataSource Z** expuesto para ODP (`RODPS_OS_EXPOSE`) y publicado igual.
3. Una **CDS** con `@Analytics.dataExtraction.enabled: true` y delta, contexto ABAP_CDS.

Y un usuario técnico de comunicación con `S_SERVICE` sobre esos servicios. Se recomienda hacerlo en **DEV o QAS**, nunca en PRD.

## Ejecución

```bash
uv run tools/spike-sap/spike_odp.py --base-url https://<gateway>:<puerto> --client <mandante> --service <SERVICIO_SRV> --entity-set <EntityOf...|FactsOf...> --user <USUARIO_TECNICO>
```

- La contraseña se pide por teclado (o variable `SPIKE_SAP_PASSWORD`); no se guarda en ningún archivo.
- En el paso 4 el script se detiene: genere cambios en SAP para ese origen y pulse Enter.
- Opciones útiles: `--page-size 1000|5000|20000` (medir rendimiento), `--filter "RBUKRS eq '1000'"`, `--max-pages 50` (acotar la init), `--mask-fields CAMPO1,CAMPO2` (enmascarar en las muestras), `--ca-bundle ca.pem`.

Resultados en `tools/spike-sap/output/<fecha>/` (ignorado por git):

| Archivo | Uso |
|---|---|
| `report.md` | Resumen para pegar en el ADR 0002 |
| `results.json` | Datos completos (tiempos, tokens, modos de cambio) |
| `metadata.xml`, `*.sample.json` | Respuestas reales (pocas filas) para alimentar `tools/sap-mock` — **revisar y anonimizar antes de versionar** |

Después de cada prueba, revise en **ODQMON** las suscripciones creadas y elimínelas.

## Prueba en local contra el mock

```bash
docker compose up -d sap-mock
SPIKE_SAP_PASSWORD=sxmock uv run tools/spike-sap/spike_odp.py --base-url http://localhost:8081 --service ZFI_WS_GL_ITEMS_SRV --entity-set EntityOf0FI_GL_14 --user sxmock --non-interactive --trigger-url http://localhost:8081/_mock/services/ZFI_WS_GL_ITEMS_SRV/changes
```
