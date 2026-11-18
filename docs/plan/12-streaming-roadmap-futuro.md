# 12 · Streaming y evolución futura

**Estado:** fuera del alcance inicial. Este documento fija cómo **no cerrarse puertas** en v1 y qué se haría después.

## 1. Streaming de datos

### Objetivo

Pasar de cargas por lotes programadas (minutos/horas) a **propagación casi en tiempo real** (segundos) de cambios de SAP hacia los destinos.

### Opciones a evaluar

| Opción | Descripción | Pros | Contras |
|---|---|---|---|
| **Micro-lotes ODP** | Pipelines delta con frecuencia muy alta (p. ej. cada 1 min) sobre la arquitectura actual | Sin infraestructura nueva | Latencia ≥ 1 min; carga sobre ODQ/Gateway |
| **SAP Event Mesh / Advanced Event Mesh** | Eventos de negocio de S/4HANA (p. ej. pedido creado) publicados a un broker | Nativo SAP, en tiempo real | Requiere BTP y eventos disponibles; eventos ≠ registros completos |
| **SLT (SAP Landscape Transformation) vía ODP** | Replicación por *triggers* de tablas, expuesta como ODP (contexto SLT) | Captura a nivel de tabla, baja latencia | Licencia/infraestructura SLT |
| **CDC de base de datos** (HANA) | Herramientas de terceros sobre la BD de SAP | Latencia mínima | Normalmente no permitido por licencia SAP; descartado salvo validación legal |

### Arquitectura objetivo

```
SAP (Event Mesh / SLT / ODP micro-lote)
      │
      ▼
Conector de streaming (nuevo tipo de worker, proceso de larga duración)
      │
      ▼
Broker de mensajes (Kafka / Redpanda / Azure Event Hubs)
      │
      ├─► Sink SQL Server (upsert continuo)
      ├─► Sink Snowflake (Snowpipe Streaming)
      └─► Otros consumidores corporativos
```

### Lo que v1 ya deja preparado

- Interfaces `SourceConnector` / `DestinationWriter` basadas en **lotes Arrow**: un flujo continuo es una secuencia de lotes pequeños.
- Tipo de pipeline extensible (`mode` admitirá `STREAMING`).
- Workers con pools: se añadiría un pool `streaming` con procesos de larga vida y heartbeat ya existentes.
- Monitoreo por métricas: se añadirían *lag* y eventos/seg.

## 2. Otras evoluciones

| Tema | Descripción | Prioridad sugerida |
|---|---|---|
| Multi-destino | Un origen/delta → varios destinos con una sola suscripción ODQ (fan-out desde el spool) | Alta (v1.1) |
| Dependencias y DAGs | Encadenar pipelines; vista de grafo; ejecución condicional | Alta (v1.1–v1.2) |
| Destinos nuevos | Azure Data Lake / Blob (Parquet/Delta), S3, PostgreSQL, Databricks, Microsoft Fabric | Media |
| Transformaciones ligeras | Columnas calculadas, conversión de unidades/moneda, filtros por fila | Media |
| Calidad de datos | Reglas (no nulos, rangos, unicidad) con resultados en el run | Media |
| Catálogo / linaje | Exponer linaje SAP → destino (OpenLineage) | Media |
| Ámbito por áreas | Permisos por etiqueta/área de negocio | Media |
| Multi-tenant / multi-sociedad | Aislamiento por sociedad o país | Baja |
| API pública / SDK | Gestionar pipelines como código (YAML/GitOps) | Media |
| Otros orígenes no SAP | Bases de datos, APIs REST | Baja |

## 3. Criterios para iniciar el streaming

- v1 estable en PRD ≥ 3 meses.
- Caso de negocio con necesidad real de latencia < 5 min.
- Validación con SAP/Basis de la vía (Event Mesh o SLT) y su licenciamiento.
