# ROADMAP · Iteraciones de implementación

Cada iteración termina con algo **demostrable**, CI en verde, pruebas, auditoría de las acciones nuevas y documentación actualizada. Duración orientativa: 2 semanas por iteración (a ajustar con el equipo).

> Actualizado el 19.11.2026 con las decisiones del [plan 13](13-revision-y-plan-de-cambios.md): dos desarrolladores, Snowflake como primer destino, instancia única, sin multi-destino y SSO obligatorio en PRD.

## Vista general

```
It0  Fundaciones + Spike SAP ─────────┐
It1  Identidad local + RBAC + Auditoría│
It1b Integración de plantilla (cuando llegue) ── flotante
It2  Conexiones SAP ──────────────────┤
It3  Orígenes SAP ────────────────────┤
It4a Destinos: CSV → Snowflake ───────┤
It5  Motor de extracción y deltas ◄───┘  ← primer flujo SAP→destino de punta a punta
It6  Orquestación y programación
It4b Destino: SQL Server
It7  SSO Microsoft Entra ID              ← obligatorio antes de PRD
It8  Monitoreo y alertas
It9  Endurecimiento, despliegue QA y PRD, runbooks   ══► v1.0
──────────────────────────────────────────────────────────
v1.1 Dependencias · ámbito por área · Graph sync
v2   Streaming (plan 12)
```

## Detalle

| It | Nombre | Planes | Entregable demostrable | Depende de |
|---|---|---|---|---|
| **0** ✅ | Fundaciones + Spike SAP (falta ejecutar el spike en el SAP real: empieza en la Fase 0) | [01](01-fundaciones.md), [10](10-frontend-y-ux.md) §0 | `docker compose up` con API, front, BD, mock SAP; CI; ADR 0002 con el comportamiento real del delta en el SAP de AG | — |
| **1** ✅ | Identidad local, RBAC y auditoría base | [02](02-usuarios-autenticacion-roles.md) (local), [03](03-auditoria-y-logs.md) (base) | Login admin local + MFA; gestión de usuarios y roles; pantalla de auditoría con integridad verificable | 0 |
| **1b** ✅ | Estilo visual propio (paleta Mega Lucario; falta el logo definitivo) | [10](10-frontend-y-ux.md) §1, [ADR 0007](../adr/0007-estilo-visual-tactil.md) | Layout y design system «táctil», temas claro y grafito, densidad compacta; `/ui-kit` | 0 |
| **2** ✅ | Conexiones SAP (falta validar contra el SAP real) | [04](04-conexiones-sap.md) | Alta de sistema SAP, prueba de conexión por pasos, explorador de servicios ODP/CDS | 1 |
| **3** ✅ | Orígenes SAP (falta revisar la guía con el equipo SAP y cambiar el mock por respuestas reales del spike) | [05](05-origenes-sap.md) | Asistente de origen (extractor estándar, Z y CDS), esquema versionado, vista previa sin suscripción; guía SAP validada | 2 |
| **4a** | Destinos: CSV y Snowflake | [07](07-destinos.md) | CSV y Snowflake con prueba de conexión, DDL previsualizado y tabla de control | 1 + cuenta de pruebas de Snowflake |
| **5** 🟡 | Motor y deltas (construido contra el mock el 21.11.2026 sobre la It6; falta validar en el SAP real tras el spike y en Snowflake) | [06](06-motor-extraccion-y-deltas.md), [03](03-auditoria-y-logs.md) (logs de ejecución) | Ejecución manual: init + delta + repetición desde SAP a CSV y Snowflake; logs en vivo; pruebas de caída en cada paso | 3, 4a, spike |
| **6** | Orquestación (backend ✅ adelantado el 21.11.2026 y conectado al motor de la It5; faltan el asistente de pipeline y la programación en pantalla) | [08](08-orquestacion-y-programacion.md) | Asistente de pipelines, programación cron/simple, cola, workers, reintentos, pausa, clonado entre entornos | 5 |
| **4b** | Destino: SQL Server | [07](07-destinos.md) | SQL Server con las mismas garantías que Snowflake | 5 |
| **7** | Entra ID (7a configuración ✅ adelantada el 20.11.2026; falta 7b, inicio de sesión) | [02](02-usuarios-autenticacion-roles.md) (Entra) | SSO configurable desde la UI, JIT, grupos → roles; admin local sigue funcionando | 1 + *App registration* de IT |
| **8** | Monitoreo y alertas | [09](09-monitoreo-y-alertas.md) | Dashboard en vivo, frescura, alertas por correo/Teams, `/metrics` | 6 |
| **9** | Endurecimiento y PRD | [11](11-despliegue-y-operacion.md) | Despliegue QA y PRD, SSO activo en PRD, backups probados, runbooks, revisión de seguridad → **v1.0** | Todas |

### Hitos

| Hito | Al terminar | Valor |
|---|---|---|
| **H1 – Plataforma base** | It1 | Acceso seguro, usuarios, auditoría |
| **H2 – Primer dato** | It5 | Datos SAP con delta llegando a Snowflake y CSV |
| **H3 – Autónoma** | It6 | Extracciones programadas sin intervención |
| **H4 – v1.0 productiva** | It9 | En PRD con SSO, monitoreo y alertas |

## Ejecución con dos desarrolladores

Dev A lleva el flujo SAP y Dev B los destinos y la plataforma. Los dos construyen el motor, partido por el spool: uno extrae hasta el spool, el otro carga desde el spool. El razonamiento, los acuerdos de trabajo y qué hacer si falla una dependencia externa están en el [plan 13 §5](13-revision-y-plan-de-cambios.md#5-plan-de-ejecución-con-dos-desarrolladores).

| Fase | Duración | Dev A (flujo SAP) | Dev B (destinos y plataforma) | Entregable |
|---|---|---|---|---|
| **0 · Preparación** | 1 semana | Spike ampliado con el equipo SAP; respuestas reales al mock; diseño del estado de delta | ✅ Seguridad de conectores (hosts permitidos, proxies de confianza); ✅ E2E y cobertura en CI; ✅ stack del frontend; pedir cuenta de Snowflake y *App registration* (borradores en [`docs/solicitudes/fase-0.md`](../solicitudes/fase-0.md)) | Juntos: ADR 0006 con los contratos del motor |
| **1 · Origen y destino** | 2–3 semanas | **It3** Orígenes | ✅ **It4a** CSV y Snowflake (falta validar Snowflake con la cuenta de pruebas y su job de CI) | Origen y destino creados desde la UI |
| **2 · Motor** | 3 semanas | **It5**, extracción: conectores, paginación, spool, máquina de delta | **It5**, carga: `load`, `commit_delta`, idempotencia, inyección de fallos, pantalla mínima de run | **H2 · Primer dato** |
| **3 · Orquestación** | 3 semanas | ✅ **It6** backend (adelantada; se conecta al motor al cerrar la It5) | **It6** pantallas; después **It4b** SQL Server | **H3 · Autónoma** |
| **4 · Operación** | 2–3 semanas | **It8** backend; prueba de volumen de 10 M de filas | **It7** Entra ID; dashboard y pantallas de alertas | SSO, alertas y dashboard |
| **5 · Endurecimiento** | 2 semanas | **It9**, entre los dos | | **H4 · v1.0** |
| Flotante | — | — | ✅ **It1b** hecha (estilo propio, paleta Mega Lucario); queda el logo definitivo | Layout definitivo y `/ui-kit` |

Total estimado: 13–15 semanas, más It1b. Las duraciones se recalculan al cerrar la Fase 1 con la velocidad real.

Dependencias externas que hay que pedir en la Fase 0: el spike con el equipo SAP y Basis, la cuenta y base de pruebas de Snowflake (ruta crítica de H2) y el *App registration* de Entra ID (ruta crítica de v1.0).

## Definición de "Hecho" (DoD) por iteración

- [ ] Código revisado en PR por la otra persona, CI en verde (lint, tipos, pruebas, build, escaneo de seguridad).
- [ ] Cobertura ≥ 80 % en `domain`/`application` del módulo, exigida en CI con `fail_under`.
- [ ] Migraciones Alembic incluidas y probadas (upgrade y downgrade), con una sola cabeza (*head*).
- [ ] Endpoints con permisos y eventos de auditoría.
- [ ] Pantallas usando solo `shared/ui`, con E2E del flujo principal ejecutado en CI (Playwright contra el mock).
- [ ] Documentación: plan del módulo actualizado con lo realmente construido, ADRs y runbooks si aplica.
- [ ] Demo al usuario/negocio.

It1 e It2 se marcaron como hechas antes de que CI exigiera cobertura y E2E; esos dos controles se añaden en la Fase 0 y se les aplican entonces.

## Riesgos principales

| Riesgo | Mitigación |
|---|---|
| Comportamiento del delta ODP-OData distinto al esperado (repetición, suscripciones compartidas) | Spike SAP en la Fase 0, antes de diseñar el motor en detalle |
| Dos pipelines delta sobre el mismo origen no conviven en SAP (un segundo destino es un segundo pipeline) | Hipótesis H6 y H18 del spike, las primeras en comprobarse; alternativas en el plan 06 §3.3 |
| Rendimiento de OData insuficiente para volúmenes grandes | Medición en el spike; ajuste de tamaño de página; ventanas de carga; paralelismo entre pipelines; estrategia de init grande (plan 06 §6) |
| Dependencia del equipo SAP para publicar servicios | Guía de publicación temprana y backlog compartido |
| La cuenta de pruebas de Snowflake no llega a tiempo | Pedirla en la Fase 0; mientras, CSV y SQL Server en contenedor |
| Plantilla con tecnología incompatible (p. ej. jQuery) | Capa `shared/ui` que aísla las pantallas; portar estilos, no código |
| Permisos de Entra ID (consentimiento de administrador): sin SSO no hay salida a PRD | Pedir el *App registration* a IT en la Fase 0 aunque se implemente en It7 |
| Una sola instancia para todos los entornos SAP: un error afecta a todos y las pruebas compiten con PRD | Despliegue de QA para validar versiones; *pool* de workers reservado para PRD; comprobación antes de activar (plan 08 §5) |
| Pérdida de la clave maestra o de la BD de metadatos | Custodia en gestor de secretos, PITR y pruebas de restauración |
