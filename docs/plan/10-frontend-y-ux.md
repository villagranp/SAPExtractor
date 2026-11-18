# 10 · Frontend y experiencia de usuario

**Iteraciones:** 0 (esqueleto), 1b (integración de plantilla) y transversal en cada módulo · **Depende de:** 01 y la **plantilla** que se entregará

## Objetivo

Una interfaz **visual e intuitiva** en la que un usuario no experto en APIs pueda conectar SAP, crear orígenes, diseñar pipelines y monitorizarlos, integrando la **plantilla corporativa** que se entregará más adelante sin acoplar las pantallas a ella.

## 1. Estrategia de integración de la plantilla

La plantilla aún no está disponible. Para no bloquear ni reescribir después:

1. **Capa de design system propia** en `src/shared/ui/` con componentes de uso interno: `Button`, `Input`, `Select`, `DataTable`, `Modal`, `Drawer`, `Tabs`, `Card`, `Badge/StatusPill`, `Stepper`, `Toast`, `EmptyState`, `PageHeader`, `FormField`, `ConfirmDialog`, etc.
2. Las pantallas (`features/*`) **solo importan de `shared/ui`**, nunca de la librería/plantilla directamente.
3. **Tokens de diseño** (colores, tipografía, espaciados, radios, sombras) como variables CSS en `src/shared/theme/tokens.css`, con modo claro/oscuro.
4. Al recibir la plantilla:
   - Análisis: tecnología (HTML/Bootstrap, React, Tailwind, Angular…), layout, componentes y activos de marca.
   - Mapear tokens y reimplementar `shared/ui` con su estilo (o envolviendo sus componentes si es React).
   - Sustituir el *layout* provisional (menú lateral, cabecera, migas de pan) por el de la plantilla.
   - Si la plantilla es de otro framework, se porta su HTML/CSS a componentes React (no se mezcla jQuery/otros frameworks en la SPA).
5. Página interna **`/ui-kit`** (solo DEV) con todos los componentes para validar visualmente la integración.

## 2. Arquitectura del frontend

```
src/
  app/            router.tsx, providers.tsx, layout/ (AppShell, Sidebar, Topbar), guards
  shared/
    api/          cliente generado (openapi-typescript) + wrapper con CSRF, errores RFC 9457
    ui/           design system
    theme/        tokens, tema claro/oscuro
    hooks/        useSSE, usePermissions, useDebounce…
    i18n/         es (defecto), en
    utils/
  features/
    auth/  users/  roles/  identity-provider/  audit/
    sap-systems/  sources/  destinations/  pipelines/  runs/  dashboard/  alerts/  settings/
      ├── api/        hooks TanStack Query (useSources, useCreateSource…)
      ├── components/
      ├── pages/
      └── schemas/    Zod
```

Reglas:

- Estado de servidor con **TanStack Query**; estado de UI local con `useState`/`useReducer`; sin store global salvo sesión/tema.
- Formularios con **React Hook Form + Zod**; errores del backend (`errors[]` de Problem Details) se mapean a campos.
- **Rutas protegidas** por permiso (`<RequirePermission perm="pipeline:manage">`); elementos ocultos/deshabilitados según `GET /me`.
- Carga diferida por módulo (`React.lazy`) y *code splitting*.
- Accesibilidad **WCAG 2.1 AA**: navegación por teclado, foco visible, `aria-*`, contraste.
- Diseño *responsive* (uso principal en escritorio; consulta del dashboard en tablet/móvil).
- Fechas mostradas en la zona horaria del usuario con indicación de la zona del schedule.

## 3. Mapa de navegación

```
Inicio (Dashboard)
Pipelines
  ├─ Lista
  ├─ Diseñador (nuevo / editar)
  └─ Detalle → Ejecuciones · Delta · Programación · Actividad
Ejecuciones (historial global) → Detalle de run
Orígenes SAP
  ├─ Sistemas SAP → Asistente · Detalle (Servicios)
  └─ Orígenes → Asistente · Detalle (Esquema, Vista previa)
Destinos → Asistente · Detalle
Alertas → Abiertas · Reglas · Canales
Administración
  ├─ Usuarios · Roles · Proveedor de identidad (Entra ID) · Tokens API
  ├─ Auditoría
  └─ Ajustes (zona horaria, retención, políticas de contraseña, workers/grupos de concurrencia)
Mi perfil · Mis sesiones
```

## 4. Experiencias clave

### 4.1 Asistentes paso a paso

Usados en: sistema SAP, origen, destino, pipeline rápido. Patrón común (`Stepper`):

- Validación por paso; no se avanza con errores.
- Botón **Probar** antes de guardar (conexiones, destinos).
- Paso final de **Resumen** legible.
- Borrador guardado automáticamente (se puede retomar).

### 4.2 Diseñador de pipelines (lienzo)

- **React Flow**: paneles laterales "Orígenes" y "Destinos" con búsqueda; arrastrar al lienzo; conectar.
- Nodos con estado (icono de tipo, delta ✔, último resultado).
- Panel derecho de propiedades: filtros (constructor visual), mapeo de columnas (tabla con renombrado/exclusión/tipo), modo de carga, programación.
- Barra de **validación en vivo** (errores y avisos del backend `POST /pipelines/{id}/validate`).
- Botones: *Guardar borrador* · *Probar* · *Activar*.
- v1: un origen → un destino por pipeline; el lienzo ya admite varios nodos para evolucionar a multi-destino y DAG.

### 4.3 Constructor de programación

- Modo simple (frases): "Cada [30] [minutos]", "Diario a las [06:00]", "De lunes a viernes a las…".
- Modo cron con ayuda y validación.
- Vista previa de **próximas 10 ejecuciones**, zona horaria seleccionable, ventanas de exclusión en calendario.

### 4.4 Monitorización en vivo

- Hook `useSSE('/api/v1/events/stream')` que actualiza la caché de TanStack Query.
- Progreso de runs, nuevas alertas (toast + campana), estado de workers.

### 4.5 Acciones peligrosas

- Reinicializar delta, eliminar, desactivar usuario, rotar credenciales: **diálogo de confirmación** que exige escribir el nombre del recurso y un **motivo** (se envía a auditoría).

## 5. Calidad

| Tipo | Herramienta | Alcance |
|---|---|---|
| Unitarias | Vitest | Utilidades, esquemas Zod, hooks |
| Componentes | Testing Library | Componentes de `shared/ui` y formularios |
| Mock de API | MSW | Desarrollo y pruebas sin backend |
| E2E | Playwright | Login, alta de sistema SAP → origen → destino → pipeline → ejecución (contra `sap-mock`) |
| Accesibilidad | `axe` en Playwright | Pantallas principales |
| Visual | Página `/ui-kit` + capturas Playwright | Tras integrar la plantilla |

## Tareas

**Iteración 0**
- [ ] Proyecto Vite, estructura, providers, router, layout provisional, `gen:api`, MSW.
- [ ] `shared/ui` mínimo (botón, input, tabla, modal, toast) y tokens.

**Iteración 1b (al recibir la plantilla)**
- [ ] Análisis de la plantilla y ADR de integración.
- [ ] Tokens y `shared/ui` adaptados; layout definitivo; `/ui-kit`.
- [ ] Revisión de accesibilidad y responsive.

**Por cada módulo**
- [ ] Pantallas descritas en su plan, usando solo `shared/ui`.
- [ ] Pruebas de componentes y E2E del flujo principal.

## Criterios de aceptación

- Cambiar la plantilla/tema no requiere modificar archivos de `features/`.
- Un usuario nuevo completa "conectar SAP → crear origen → crear destino → programar pipeline" sin documentación externa, en < 15 minutos (prueba de usabilidad con 2–3 usuarios).
- Lighthouse accesibilidad ≥ 90 en las pantallas principales.
