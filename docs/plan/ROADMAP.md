# ROADMAP · Iteraciones de implementación

Cada iteración termina con algo **demostrable**, CI en verde, pruebas, auditoría de las acciones nuevas y documentación actualizada. Duración orientativa: 2 semanas por iteración (a ajustar con el equipo).

## Vista general

```
It0  Fundaciones + Spike SAP ─────────┐
It1  Identidad local + RBAC + Auditoría│
It1b Integración de plantilla (cuando llegue) ── en paralelo a It2–It4
It2  Conexiones SAP ──────────────────┤
It3  Orígenes SAP ────────────────────┤
It4  Destinos (CSV → SQL Server → Snowflake)
It5  Motor de extracción y deltas ◄───┘  ← primer flujo SAP→destino de punta a punta
It6  Orquestación y programación
It7  SSO Microsoft Entra ID
It8  Monitoreo y alertas
It9  Endurecimiento, despliegue PRD, runbooks   ══► v1.0
──────────────────────────────────────────────────────────
v1.1 Multi-destino · dependencias · ámbito por área · Graph sync
v2   Streaming (plan 12)
```

## Detalle

| It | Nombre | Planes | Entregable demostrable | Depende de |
|---|---|---|---|---|
| **0** ✅ | Fundaciones + Spike SAP (falta ejecutar el spike en el SAP real) | [01](01-fundaciones.md), [10](10-frontend-y-ux.md) §0 | `docker compose up` con API, front, BD, mock SAP; CI; ADR 0002 con el comportamiento real del delta en el SAP de AG | — |
| **1** ✅ | Identidad local, RBAC y auditoría base | [02](02-usuarios-autenticacion-roles.md) (local), [03](03-auditoria-y-logs.md) (base) | Login admin local + MFA; gestión de usuarios y roles; pantalla de auditoría con integridad verificable | 0 |
| **1b** | Integración de plantilla | [10](10-frontend-y-ux.md) §1 | Layout y design system con la plantilla corporativa; `/ui-kit` | 0 + plantilla recibida |
| **2** ✅ | Conexiones SAP (falta validar contra el SAP real) | [04](04-conexiones-sap.md) | Alta de sistema SAP, prueba de conexión por pasos, explorador de servicios ODP/CDS | 1 |
| **3** | Orígenes SAP | [05](05-origenes-sap.md) | Asistente de origen (extractor estándar, Z y CDS), esquema versionado, vista previa sin suscripción; guía SAP validada | 2 |
| **4** | Destinos | [07](07-destinos.md) | CSV, SQL Server y Snowflake con prueba de conexión y DDL previsualizado | 1 |
| **5** | Motor y deltas | [06](06-motor-extraccion-y-deltas.md), [03](03-auditoria-y-logs.md) (logs de ejecución) | Ejecución manual: init + delta + repetición desde SAP a los 3 destinos; logs en vivo; pruebas de caída en cada paso | 3, 4 |
| **6** | Orquestación | [08](08-orquestacion-y-programacion.md) | Diseñador visual de pipelines, programación cron/simple, cola, workers, reintentos, pausa | 5 |
| **7** | Entra ID | [02](02-usuarios-autenticacion-roles.md) (Entra) | SSO configurable desde la UI, JIT, grupos → roles; admin local sigue funcionando | 1 (puede adelantarse si IT entrega el *App registration* antes) |
| **8** | Monitoreo y alertas | [09](09-monitoreo-y-alertas.md) | Dashboard en vivo, frescura, alertas por correo/Teams, `/metrics` | 6 |
| **9** | Endurecimiento y PRD | [11](11-despliegue-y-operacion.md) | Despliegue QA/PRD, backups probados, runbooks, revisión de seguridad → **v1.0** | Todas |

### Hitos

| Hito | Al terminar | Valor |
|---|---|---|
| **H1 – Plataforma base** | It1 | Acceso seguro, usuarios, auditoría |
| **H2 – Primer dato** | It5 | Datos SAP con delta llegando a SQL Server/CSV/Snowflake |
| **H3 – Autónoma** | It6 | Extracciones programadas sin intervención |
| **H4 – v1.0 productiva** | It9 | En PRD con SSO, monitoreo y alertas |

## Trabajo en paralelo posible

- **It1b** (plantilla) en cuanto llegue, en paralelo a It2–It4.
- **It4** (destinos) puede ir en paralelo a **It2–It3** (no dependen entre sí).
- **It7** (Entra ID) es independiente del flujo de datos; puede adelantarse.
- El equipo SAP puede publicar los servicios ODP/CDS reales desde It0 (spike) hasta It3.

## Definición de "Hecho" (DoD) por iteración

- [ ] Código revisado en PR, CI en verde (lint, tipos, pruebas, build, escaneo de seguridad).
- [ ] Cobertura ≥ 80 % en `domain`/`application` del módulo.
- [ ] Migraciones Alembic incluidas y probadas (upgrade y downgrade).
- [ ] Endpoints con permisos y eventos de auditoría.
- [ ] Pantallas usando solo `shared/ui`, con E2E del flujo principal.
- [ ] Documentación: plan del módulo actualizado con lo realmente construido, ADRs y runbooks si aplica.
- [ ] Demo al usuario/negocio.

## Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Comportamiento del delta ODP-OData distinto al esperado (repetición, suscripciones compartidas) | Spike SAP en It0 antes de diseñar el motor en detalle |
| Rendimiento de OData insuficiente para volúmenes grandes | Medición en el spike; ajuste de tamaño de página; ventanas de carga; paralelismo entre pipelines |
| Dependencia del equipo SAP para publicar servicios | Guía de publicación temprana y backlog compartido |
| Plantilla con tecnología incompatible (p. ej. jQuery) | Capa `shared/ui` que aísla las pantallas; portar estilos, no código |
| Permisos de Entra ID (consentimiento de administrador) | Pedir el *App registration* a IT al inicio aunque se implemente en It7 |
| Pérdida de la clave maestra o de la BD de metadatos | Custodia en gestor de secretos, PITR y pruebas de restauración |
