# 11 · Despliegue y operación

**Iteración:** 9 (endurecimiento) y transversal desde la 0 · **Depende de:** todos

## Objetivo

Desplegar SAPExtract de forma repetible y segura, con procedimientos claros de operación, respaldo y recuperación.

> Actualizado el 19.11.2026 con los cambios C-04, C-07, C-17 y C-20 del [plan 13](13-revision-y-plan-de-cambios.md).

## 1. Entornos

Hay **una sola instancia productiva** de la plataforma, conectada a los sistemas SAP de todos los entornos (decisión del 19.11.2026). Los demás despliegues sirven para validar versiones de la propia plataforma, no para atender a un entorno SAP.

| Despliegue | Uso | SAP conectado | Inicio de sesión |
|---|---|---|---|
| DEV | Desarrollo de la plataforma | `sap-mock` | Local |
| QA | Validar cada versión antes de pasarla a PRD | `sap-mock`; SAP real solo con usuario técnico propio | Local (Entra ID opcional) |
| PRD | Instancia única de trabajo | SAP DEV, QAS y PRD | **Entra ID activo**; el administrador local queda como acceso de emergencia |

Promoción de la **misma imagen** de contenedor entre despliegues (etiquetada por versión semántica + commit); solo cambia la configuración.

Reglas de la instancia única:

- **DEV y QA no abren deltas sobre los servicios que usa PRD** con el mismo usuario técnico: competirían por la misma suscripción en SAP (hipótesis H6 y H18 del spike).
- **Entorno por objeto.** Cada sistema SAP y cada destino llevan su entorno (`DEV`, `QAS`, `PRD`). Un pipeline que cruza entornos exige confirmación con motivo (plan 08 §5).
- **Capacidad reservada.** Un *pool* de workers atiende solo pipelines de PRD, y cada sistema SAP tiene su límite de runs simultáneos (plan 08 §3).
- **Mismo flujo en cada entorno.** Un pipeline se valida en DEV, se clona a QAS y luego a PRD; en cada paso se compara el esquema y se repite la comprobación previa a activar (plan 08 §5). Los límites de Gateway, la retención de la ODQ y el rendimiento se miden en cada sistema: DEV no representa a PRD.
- **Riesgos aceptados.** Las credenciales de los tres entornos SAP están en la misma base (cifradas con la clave maestra), y una caída de la instancia detiene las cargas de todos los entornos.

## 2. Topología de producción (por defecto: Linux + Docker)

| Componente | Instancias | Notas |
|---|---|---|
| Reverse proxy (Nginx) | 1–2 | TLS con certificado corporativo, HSTS, sirve el frontend, `/api` → API |
| `api` | 2 | Sin estado |
| `scheduler` | 2 | 1 líder + 1 en espera |
| `worker` | 2–N | Escalar según volumen; al menos uno en el *pool* de PRD. Volumen de **spool** en disco local rápido de cada worker, cifrado por la infraestructura: contiene datos de SAP en claro. Si un reintento cae en otro worker, vuelve a extraer (plan 06 §2); un volumen compartido es la alternativa si eso resulta caro |
| PostgreSQL 16 | 1 primario (+ réplica opcional) | Gestionado (Azure Database for PostgreSQL) o en servidor propio con backups |
| Almacenamiento de logs | — | stdout → colector corporativo, o archivos rotados |

Requisitos de red: salida HTTPS hacia SAP Gateway (DEV, QAS y PRD), Snowflake (443), SQL Server (1433), Entra ID (`login.microsoftonline.com`), SMTP/Graph/Teams; entrada HTTPS solo al proxy. El puerto de la API no se publica fuera de la red del proxy.

### Alternativa Windows Server (decisión D2)

- Opción A: Docker en Windows Server con contenedores Linux (WSL2) — mismo `compose`.
- Opción B (sin contenedores): Python + servicios de Windows (NSSM / `pywin32`) para `api`, `scheduler`, `worker`; IIS o Nginx como proxy para estáticos y `/api`. Se documentaría en un runbook específico si se elige.

## 3. Configuración y secretos

- Variables de entorno `SX_*` (plantilla `.env.example` documentada, sin valores reales).
- **Clave maestra** (`SX_MASTER_KEY`) fuera del repositorio: gestor de secretos del host o **Azure Key Vault** (soportado por configuración).
- Rotación: comando `sapextract secrets rotate-master-key` (re-cifra claves de datos; auditado).
- Credenciales de SAP/destinos/IdP **solo en BD cifradas**, gestionadas desde la UI.
- Variables de seguridad de red, obligatorias en PRD:

  | Variable | Para qué |
  |---|---|
  | `SX_TRUSTED_PROXIES` | Red del proxy inverso. Solo las peticiones que llegan de ahí pueden fijar la IP del cliente (auditoría y límite de intentos). Sustituye a `--forwarded-allow-ips "*"` de la imagen actual; por defecto, solo *loopback* |
  | `SX_SAP_ALLOWED_HOSTS` | Hosts a los que se permite conectar como sistema SAP (plan 04 §3) |
  | `SX_DEST_ALLOWED_HOSTS` | Hosts permitidos para destinos y *webhooks* de alertas (planes 07 y 09) |
  | `SX_CSV_ROOTS` | Carpetas donde el destino CSV puede escribir (plan 07 §4) |

## 4. Base de datos y migraciones

- Alembic `upgrade head` como paso explícito del despliegue (contenedor *one-shot* `migrate`), nunca automático en cada réplica.
- Migraciones **compatibles hacia atrás** (expand/contract) para permitir despliegue sin parada.
- Usuario de BD de aplicación sin permisos DDL en producción; usuario de migración separado.

## 5. Respaldo y recuperación

| Elemento | Estrategia | RPO / RTO objetivo |
|---|---|---|
| PostgreSQL (metadatos, delta tokens, auditoría) | Backup completo diario + WAL continuo (PITR) | RPO 15 min / RTO 4 h |
| Clave maestra | Custodia en gestor de secretos con respaldo | Crítica: sin ella no se descifran credenciales |
| Spool | No se respalda y queda fuera de los backups del host: temporal, reproducible y con datos en claro. Se borra al terminar el run; retención máxima de 72 h | — |
| Configuración | Exportación JSON de sistemas/orígenes/destinos/pipelines (sin secretos), como respaldo. Con instancia única no se usa para promocionar entre entornos | — |

> Los **delta tokens** viven en la base de metadatos: perderlos obliga a reinicializar deltas en SAP. Por eso la BD de metadatos es crítica y se prueba la restauración trimestralmente.

## 6. Seguridad operativa

- Contenedores sin root, sistema de archivos de solo lectura salvo `spool` y `logs`.
- Escaneo de imágenes (Trivy) en CI; dependencias auditadas.
- Cabeceras de seguridad y TLS 1.2+ en el proxy.
- Pentest / revisión de seguridad antes del primer pase a PRD.
- Usuario técnico SAP de solo lectura (tipo *System*, sin diálogo). Su autorización de lectura es amplia por decisión del 19.11.2026, para poder extraer cualquier origen; el acceso a los datos se limita con los permisos de la plataforma, que solo tienen personas capacitadas.
- PRD no se abre a usuarios sin Entra ID activo y probado.
- El proxy inverso es el único origen de confianza para las cabeceras `X-Forwarded-*` (`SX_TRUSTED_PROXIES`).

## 7. Runbooks (`docs/runbooks/`)

1. Instalación inicial y creación del administrador local.
2. Configuración de Entra ID (App registration paso a paso).
3. Alta de un sistema SAP y publicación de servicios (enlace al plan 05 §5).
4. Pipeline en `BROKEN`: diagnóstico y reinicialización de delta (incluye ODQMON). Cubre también la suscripción estancada (pipeline delta pausado o roto durante días) y el cierre manual de suscripciones en SAP.
5. Worker/scheduler caído.
6. Restauración de base de metadatos.
7. Rotación de clave maestra y de credenciales.
8. Actualización de versión (migraciones + despliegue sin parada).
9. Recuperación del acceso de administrador (CLI).
10. Llevar un pipeline de DEV a QAS y a PRD: clonado, diff de esquema, comprobación previa y activación.

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
- [ ] Runbooks 1–10.
- [ ] Pipeline CD con aprobaciones.
- [ ] Revisión de seguridad previa a PRD.
- [x] `SX_TRUSTED_PROXIES` en lugar de `--forwarded-allow-ips "*"` (Fase 0, plan 13 C-07 d). La API aplica `X-Forwarded-For`/`Proto` solo si la petición llega de esas IP o redes (por defecto `127.0.0.1`); uvicorn arranca con `--no-proxy-headers` para no aplicarlo dos veces. `"*"` se rechaza en QA y PRD. En el despliegue hay que indicar la red del Nginx.
- [ ] Permisos, retención y exclusión de backups del directorio de spool.
- [ ] Entra ID configurado y probado en PRD antes de abrir el acceso.

## Criterios de aceptación

- Despliegue de una versión nueva en QA en < 15 min sin intervención manual salvo aprobación.
- Restauración probada de la BD de metadatos con deltas que continúan correctamente.
- Ningún secreto en imágenes, repositorio ni logs (escaneo en CI).
- En PRD los usuarios entran con Entra ID; el administrador local de emergencia funciona aunque Entra ID falle.
- Una petición directa a la API con `X-Forwarded-For` falsificado no cambia la IP que registra la auditoría.
