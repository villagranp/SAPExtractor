# 11 · Despliegue y operación

**Iteración:** 9 (endurecimiento) y transversal desde la 0 · **Depende de:** todos

## Objetivo

Desplegar SAPExtract de forma repetible y segura en DEV, QA y PRD, con procedimientos claros de operación, respaldo y recuperación.

## 1. Entornos

| Entorno | Uso | SAP conectado | Datos |
|---|---|---|---|
| DEV | Desarrollo | `sap-mock` + sistema SAP de desarrollo | Sintéticos / DEV |
| QA | Pruebas de aceptación | SAP QAS | Copia de calidad |
| PRD | Productivo | SAP PRD | Reales |

Promoción de la **misma imagen** de contenedor entre entornos (etiquetada por versión semántica + commit); solo cambia la configuración.

## 2. Topología de producción (por defecto: Linux + Docker)

| Componente | Instancias | Notas |
|---|---|---|
| Reverse proxy (Nginx) | 1–2 | TLS con certificado corporativo, HSTS, sirve el frontend, `/api` → API |
| `api` | 2 | Sin estado |
| `scheduler` | 2 | 1 líder + 1 en espera |
| `worker` | 2–N | Escalar según volumen; volumen de **spool** en disco local rápido |
| PostgreSQL 16 | 1 primario (+ réplica opcional) | Gestionado (Azure Database for PostgreSQL) o en servidor propio con backups |
| Almacenamiento de logs | — | stdout → colector corporativo, o archivos rotados |

Requisitos de red: salida HTTPS hacia SAP Gateway, SQL Server (1433), Snowflake (443), Entra ID (`login.microsoftonline.com`), SMTP/Graph/Teams; entrada HTTPS solo al proxy.

### Alternativa Windows Server (decisión D2)

- Opción A: Docker en Windows Server con contenedores Linux (WSL2) — mismo `compose`.
- Opción B (sin contenedores): Python + servicios de Windows (NSSM / `pywin32`) para `api`, `scheduler`, `worker`; IIS o Nginx como proxy para estáticos y `/api`. Se documentaría en un runbook específico si se elige.

## 3. Configuración y secretos

- Variables de entorno `SX_*` (plantilla `.env.example` documentada, sin valores reales).
- **Clave maestra** (`SX_MASTER_KEY`) fuera del repositorio: gestor de secretos del host o **Azure Key Vault** (soportado por configuración).
- Rotación: comando `sapextract secrets rotate-master-key` (re-cifra claves de datos; auditado).
- Credenciales de SAP/destinos/IdP **solo en BD cifradas**, gestionadas desde la UI.

## 4. Base de datos y migraciones

- Alembic `upgrade head` como paso explícito del despliegue (contenedor *one-shot* `migrate`), nunca automático en cada réplica.
- Migraciones **compatibles hacia atrás** (expand/contract) para permitir despliegue sin parada.
- Usuario de BD de aplicación sin permisos DDL en producción; usuario de migración separado.

## 5. Respaldo y recuperación

| Elemento | Estrategia | RPO / RTO objetivo |
|---|---|---|
| PostgreSQL (metadatos, delta tokens, auditoría) | Backup completo diario + WAL continuo (PITR) | RPO 15 min / RTO 4 h |
| Clave maestra | Custodia en gestor de secretos con respaldo | Crítica: sin ella no se descifran credenciales |
| Spool | No se respalda (temporal, reproducible) | — |
| Configuración | Exportación JSON de sistemas/orígenes/destinos/pipelines (sin secretos) | — |

> Los **delta tokens** viven en la base de metadatos: perderlos obliga a reinicializar deltas en SAP. Por eso la BD de metadatos es crítica y se prueba la restauración trimestralmente.

## 6. Seguridad operativa

- Contenedores sin root, sistema de archivos de solo lectura salvo `spool` y `logs`.
- Escaneo de imágenes (Trivy) en CI; dependencias auditadas.
- Cabeceras de seguridad y TLS 1.2+ en el proxy.
- Pentest / revisión de seguridad antes del primer pase a PRD.
- Usuario técnico SAP con mínimo privilegio y solo lectura.

## 7. Runbooks (`docs/runbooks/`)

1. Instalación inicial y creación del administrador local.
2. Configuración de Entra ID (App registration paso a paso).
3. Alta de un sistema SAP y publicación de servicios (enlace al plan 05 §5).
4. Pipeline en `BROKEN`: diagnóstico y reinicialización de delta (incluye ODQMON).
5. Worker/scheduler caído.
6. Restauración de base de metadatos.
7. Rotación de clave maestra y de credenciales.
8. Actualización de versión (migraciones + despliegue sin parada).
9. Recuperación del acceso de administrador (CLI).

## 8. Versionado y liberación

- SemVer; `CHANGELOG.md` generado desde Conventional Commits.
- Pipeline CD: build → pruebas → imagen → despliegue en DEV automático → QA con aprobación → PRD con aprobación y ventana.
- Página "Acerca de" con versión, commit y fecha de build.

## Tareas

- [ ] Dockerfiles multi-stage (backend, frontend) sin root.
- [ ] `docker-compose.prod.yml` + Nginx con TLS.
- [ ] Contenedor `migrate` y estrategia expand/contract.
- [ ] Integración opcional con Azure Key Vault.
- [ ] Backups y prueba de restauración documentada.
- [ ] Runbooks 1–9.
- [ ] Pipeline CD con aprobaciones.
- [ ] Revisión de seguridad previa a PRD.

## Criterios de aceptación

- Despliegue de una versión nueva en QA en < 15 min sin intervención manual salvo aprobación.
- Restauración probada de la BD de metadatos con deltas que continúan correctamente.
- Ningún secreto en imágenes, repositorio ni logs (escaneo en CI).
