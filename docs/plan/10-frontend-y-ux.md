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
- Formularios con **React Hook Form + Zod**; errores del backend (`errors[]` de Problem Details) se mapean a campos. Todavía no están instalados y las pantallas de It1–It2 se hicieron sin ellos: se adoptan en It3 (asistente de orígenes) y los formularios dinámicos de It4 los aprovechan. TanStack Table solo se añade si `shared/ui/data.tsx` se queda corto.
- El **entorno** (DEV, QAS, PRD) de sistemas SAP, destinos y pipelines se muestra siempre con un distintivo: la instancia es única para todos los entornos.
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
  ├─ Asistente (nuevo / editar)
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

Usados en: sistema SAP, origen, destino y pipeline. Patrón común (`Stepper`):

- Validación por paso; no se avanza con errores.
- Botón **Probar** antes de guardar (conexiones, destinos).
- Paso final de **Resumen** legible.
- Borrador guardado automáticamente (se puede retomar).

### 4.2 Asistente de pipeline

Un pipeline es un origen hacia un destino y no habrá multi-destino (decisión del 19.11.2026), así que el lienzo con React Flow previsto al inicio no se construye: un asistente hace lo mismo con menos coste y se maneja bien con teclado.

- **Pasos**: origen (con búsqueda) → destino → carga (filtros con constructor visual, mapeo de columnas en tabla con renombrado/exclusión/tipo, modo de carga) → programación → resumen.
- Cada elección muestra su estado (tipo, entorno, delta, último resultado).
- Barra de **validación en vivo** (errores y avisos del backend `POST /pipelines/{id}/validate`), incluido el aviso de entornos distintos en origen y destino.
- Botones: *Guardar borrador* · *Probar* · *Activar*.
- El detalle del pipeline muestra origen y destino en la cabecera. *Clonar* abre el asistente con el sistema y el destino de otro entorno ya elegidos y el diff de esquema.

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
| E2E | Playwright, **ejecutado en CI** con `docker compose` y el mock | Login, alta de sistema SAP → origen → destino → pipeline → ejecución (contra `sap-mock`) |
| Accesibilidad | `axe` en Playwright (por instalar) | Pantallas principales |
| Visual | Página `/ui-kit` + capturas Playwright | Tras integrar la plantilla |

## Tareas

**Iteración 0** ✅
- [x] Proyecto Vite, estructura, providers, router, layout provisional, `gen:api`, MSW.
- [x] `shared/ui` mínimo (botón, input, tabla, modal, toast) y tokens.

**Fase 0 (plan 13, C-21 y C-22)**
- [x] Job de Playwright en CI con `axe` (`tests/e2e/a11y.spec.ts`). Al activarlo apareció un contraste insuficiente (4,48:1) del verde de éxito en modo claro: el token `--sx-color-success` pasa a `#1a6e45` (5,5:1).
- [x] Instalar React Hook Form, Zod y `@hookform/resolvers`. Ayudante `shared/api/forms.ts` (`applyServerErrors`) que vuelca los `errors[]` de Problem Details en los campos del formulario. Primer uso en el asistente de orígenes (It3); los formularios de It1–It2 se migran cuando se toquen.

**Iteración 1b** ✅ (19.11.2026, [ADR 0007](../adr/0007-estilo-visual-tactil.md))
- [x] Análisis de la plantilla y ADR de integración. Referencia: Atmosphere (comercial, sin licencia): diseño **propio** con el mismo lenguaje visual, sin copiar su código.
- [x] Tokens y `shared/ui` adaptados; layout definitivo; `/ui-kit`. Material táctil (relieve, teclas, pozos), temas claro y grafito con `light-dark()`, densidad compacta, carril de teclas con iconos Tabler, fuente Onest autoalojada, inicio rediseñado con tarjetas de estado.
- [x] Revisión de accesibilidad y responsive: contrastes ≥ 4,5:1 en ambos temas, axe en CI en claro y grafito, carril en fila superior por debajo de 900 px.
- [x] Paleta: acento naranja con grises neutros (decidido el 19.11.2026), contrastes verificados en ambos temas.
- [x] Paleta Mega Lucario (20.11.2026): azul de aura, negro de pelaje, crema del pecho y rojo de ojos, en OKLCH; todos los pares ≥ 4,5:1 (foco y acento ≥ 3:1) en claro y grafito. Sustituye al naranja; colores sueltos del layout y del diálogo pasan a tokens (`--sx-fur-*`, `--sx-scrim`, `--sx-shade-strong`). Tecla deshabilitada plana y neutra (antes, acento al 55 %). Alto contraste: `prefers-contrast: more` sube la tinta secundaria y añade contornos; con `forced-colors` cada pieza recibe contorno y la selección usa `Highlight` (antes, sin sombras, no quedaba ningún límite visible).
- [x] Tecla de usuario del carril (20.11.2026): icono de usuario de Tabler en lugar de las iniciales; en claro pasa a crema del pecho con icono negro (antes, negro de pelaje); en grafito no cambia.
- [x] Login rediseñado (20.11.2026): marco con formulario e imagen de la plataforma, entorno y versión visibles antes de entrar (`/api/v1/system/info`), `h1` por paso con su subtítulo, logo sin recuadro. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde.
- [x] Revisión de la UI (20.11.2026): la página ya no se desborda en horizontal por debajo de 900 px (columna `minmax(0, 1fr)`); en esa fila se ocultan las iteraciones futuras; en escritorio muestran su nombre (cabe sin scroll desde 1366 × 830). Hoja de ruta del inicio al día (It3 hecha; Fase 0 e It4a en curso). Sentence case en la línea sobre el título y en las cabeceras de tabla; el inicio no repite el entorno. «Aún no hay sistemas SAP registrados» con cero sistemas. Destinos: modos de carga como lista natural y un solo «Nuevo destino» en el estado vacío.
- [x] Pantalla «Microsoft Entra ID» y guía del registro de aplicación (20.11.2026, It7a, plan 02): solo componentes de `shared/ui` y tokens; textos en `es.json` y `en.json`. La URI de redirección va en un campo de solo lectura con «Copiar» (no en la caja ámbar de secreto de un solo uso). El mapeo de grupos pasa a dos filas por grupo por debajo de 1180 px, para que el GUID quepa. Los selectores de rol esperan a la lista de roles y siempre incluyen los ya configurados: antes se quedaban vacíos y al guardar se perdía el rol por defecto. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde, con la API simulada (`page.route`); axe sin incumplimientos graves; contraste del bloque de comando 11,3:1 (claro) y 16,2:1 (grafito).
- [x] Entra ID más compacta (20.11.2026): los campos cortos van en parejas de dos columnas (inquilino y aplicación; secreto y caducidad; certificado y clave privada; las dos casillas de acceso; rol por defecto y dominios), una sola columna por debajo de 720 px. La URI de redirección sigue a lo ancho. A 1440 px la página pasa a medir 1719 px de alto. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde.
- [ ] Logo definitivo (hoy, monograma «SX»).

**Iteración 5** (21.11.2026, plan 06 «Lo construido en la iteración 5»)
- [x] Pipelines y Ejecuciones salen de «próximamente» en el carril (permiso `pipeline:read`). `features/pipelines`: lista con estado, alta en una sola pantalla (el asistente con programación es de la It6) que solo ofrece los modos de extracción del origen y los modos de carga seguros, y detalle con «Activar» (borrador), «Ejecutar ahora» (activo), avisos de la revisión del pipeline y pestañas Ejecuciones, Delta (estado, token, historial, reinicializar con código y motivo, confirmar el cierre en ODQMON o dejarla abierta con motivo) y Actividad.
- [x] `features/runs`: historial global y detalle de run con resumen (filas, lote, generación, tokens, ventana), log en vivo (sondeo cada segundo con cursor hasta que la API indica `finished`), pasos, intentos y cancelación con confirmación. El nivel del log se marca con un filete de color, no con el color del texto. Cifras con `Intl.NumberFormat` del idioma.
- [x] Verificado a 375, 768, 1024 y 1440 px en claro y grafito contra la pila real (worker incluido), sin desborde: las tarjetas del detalle de run van en una rejilla `minmax(0, 1fr)` (con `.sx-stack` la tabla de pasos estiraba la página a 527 px en 375). Contraste medido del log: 11,3:1 y 4,56:1 (hora y paso, claro), 16,2:1 y 8,1:1 (grafito); tokens 12,1:1 y 15,6:1. axe sin incumplimientos en `/pipelines`, `/pipelines/new` y `/runs`. E2E: pipeline delta → activar → ejecutar ahora → worker → inicialización confirmada.

**Por cada módulo**
- [ ] Pantallas descritas en su plan, usando solo `shared/ui`.
- [ ] Pruebas de componentes y E2E del flujo principal.

## Criterios de aceptación

- Cambiar la plantilla/tema no requiere modificar archivos de `features/`.
- Un usuario nuevo completa "conectar SAP → crear origen → crear destino → programar pipeline" sin documentación externa, en < 15 minutos (prueba de usabilidad con 2–3 usuarios).
- Lighthouse accesibilidad ≥ 90 en las pantallas principales.
