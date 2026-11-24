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
- [x] Nueva imagen del login y nuevo logotipo hexagonal (22.11.2026): `login-main-640/1122.webp`, `logo-64/128/192.webp` y favicons recortados al contorno del hexágono con transparencia. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde.
- [x] Revisión de la UI (20.11.2026): la página ya no se desborda en horizontal por debajo de 900 px (columna `minmax(0, 1fr)`); en esa fila se ocultan las iteraciones futuras; en escritorio muestran su nombre (cabe sin scroll desde 1366 × 830). Hoja de ruta del inicio al día (It3 hecha; Fase 0 e It4a en curso). Sentence case en la línea sobre el título y en las cabeceras de tabla; el inicio no repite el entorno. «Aún no hay sistemas SAP registrados» con cero sistemas. Destinos: modos de carga como lista natural y un solo «Nuevo destino» en el estado vacío.
- [x] Pantalla «Microsoft Entra ID» y guía del registro de aplicación (20.11.2026, It7a, plan 02): solo componentes de `shared/ui` y tokens; textos en `es.json` y `en.json`. La URI de redirección va en un campo de solo lectura con «Copiar» (no en la caja ámbar de secreto de un solo uso). El mapeo de grupos pasa a dos filas por grupo por debajo de 1180 px, para que el GUID quepa. Los selectores de rol esperan a la lista de roles y siempre incluyen los ya configurados: antes se quedaban vacíos y al guardar se perdía el rol por defecto. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde, con la API simulada (`page.route`); axe sin incumplimientos graves; contraste del bloque de comando 11,3:1 (claro) y 16,2:1 (grafito).
- [x] Entra ID más compacta (20.11.2026): los campos cortos van en parejas de dos columnas (inquilino y aplicación; secreto y caducidad; certificado y clave privada; las dos casillas de acceso; rol por defecto y dominios), una sola columna por debajo de 720 px. La URI de redirección sigue a lo ancho. A 1440 px la página pasa a medir 1719 px de alto. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde.
- [x] Pantallas de pipelines y ejecuciones (21.11.2026, It6, plan 08 «Pantallas»): lista, asistente, ficha, constructor de programación, historial y detalle de ejecución. Solo componentes de `shared/ui` y tokens; estilos propios en `features/pipelines/pipelines.css` (recorrido origen → destino, elección de origen y destino, constructor de programación). `DataTable` pasa a ser una región con nombre y enfocable cuando tiene `caption`: en pantallas estrechas la tabla se desplaza en horizontal y antes no se podía mover con el teclado. Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde, con la API simulada (`page.route`); axe sin incumplimientos en las 13 pantallas, en los dos temas, a 375 y 1440 px. El campo de ejecuciones simultáneas entra en el asistente de sistemas SAP.
- [x] Ajustes y tablas en móvil (21.11.2026): pantalla «Ajustes» con los grupos de concurrencia (plan 08). `DataTable` admite `stacked`: por debajo de 640 px cada fila pasa a ficha, con la cabecera de cada columna delante de su valor; lo usan las tablas de pipelines, ejecuciones, pasos, intentos, mapeo de columnas y grupos (el resto sigue con desplazamiento horizontal dentro de su tarjeta). El carril gana una tecla y, para seguir cabiendo sin scroll a 1366 × 830, se aprieta en ventanas de menos de 880 px de alto. Las pantallas de pipelines y ejecuciones se unieron con las que llegaron con el motor (plan 08 «Fusión»). Verificado a 375, 768, 1024 y 1440 px en claro y grafito, sin desborde; axe sin incumplimientos en las 16 pantallas.
- [x] «Probar pipeline» (21.11.2026): botón «Probar» en el detalle del pipeline y diálogo con el resultado paso a paso (`TrialDialog`, lista de comprobación de `shared/ui`). En «Comprobar y activar», cuando falta la prueba, el pie del diálogo ofrece «Probar pipeline» en lugar de «Comprobar de nuevo» y, si sale bien, la comprobación se repite sola. El resultado es una región enfocable para que el diálogo se pueda desplazar con el teclado. Capturas reales contra una pila `docker compose` aislada a 375, 768, 1024 y 1440 px, en claro y grafito: sin desborde, sin errores de consola y axe sin incumplimientos.
- [x] Dependencias entre pipelines (21.11.2026, plan 08): en el constructor de programación, «Lanzamiento» pasa a tener tres opciones («Con programación», «Después de otro pipeline», «Solo a mano»). La dependencia pide el pipeline anterior en un desplegable, explica en tres frases cómo se encadena y conserva las ventanas de exclusión; la zona horaria solo aparece si hay una ventana semanal. Lista, ficha, pestaña de programación y detalle de ejecución enlazan el pipeline o la ejecución anterior. Sin componentes nuevos en `shared/ui`; una clase nueva (`sx-pipelines__rules`). Capturas con la API simulada (`page.route`) a 375, 768, 1024 y 1440 px, en claro y grafito, de 10 pantallas: sin desborde, sin errores de consola y axe sin incumplimientos. Contraste medido de las reglas 4,56:1 (claro) y 8,1:1 (grafito); frase y título 11,28:1 y 16,24:1.
- [x] Paginación de 20 filas y cierre de sesión (23.11.2026): toda tabla abre con 20 filas y deja elegir 20, 50 o 100 en «Filas por página». `DataTable` pagina sola en el navegador cuando recibe todas las filas y acepta `pagination` cuando la API ya devuelve la página (`usePageState` guarda página y tamaño; cambiar el tamaño vuelve a la primera página). `Pagination` toma sus textos de `common.*` y no se pinta si todo cabe en 20 filas. «Cerrar sesión» ya vuelve al login: `queryClient.clear()` dejaba a `RequireAuth` observando una consulta borrada, no se enteraba de que la sesión terminó y la aplicación seguía en pantalla; ahora se borra el resto de consultas, `/me` pasa a `null` y se navega a `/login`. «Entrar» ya no se desactiva con los campos vacíos: con el autorrelleno del navegador, React no ve los valores hasta el primer clic y el botón parecía roto; ahora el formulario lee los valores al enviar y, si falta alguno, lo marca («Escriba su usuario.», «Escriba su contraseña.») y le da el foco. Las pantallas de MFA no se tocan: el MFA lo hará el SSO de Entra ID (plan 02 §3). Verificado en usuarios con 45 filas simuladas (`page.route`) a 375, 768, 1024 y 1440 px, en claro y grafito, sin desborde.
- [ ] Logo definitivo (hoy, monograma «SX»).

**Iteración 5** (21.11.2026, plan 06 «Lo construido en la iteración 5»)
- [x] Pipelines y Ejecuciones salen de «próximamente» en el carril (permiso `pipeline:read`). `features/pipelines`: lista con estado, alta en una sola pantalla (el asistente con programación es de la It6) que solo ofrece los modos de extracción del origen y los modos de carga seguros, y detalle con «Activar» (borrador), «Ejecutar ahora» (activo), avisos de la revisión del pipeline y pestañas Ejecuciones, Delta (estado, token, historial, reinicializar con código y motivo, confirmar el cierre en ODQMON o dejarla abierta con motivo) y Actividad.
- [x] `features/runs`: historial global y detalle de run con resumen (filas, lote, generación, tokens, ventana), log en vivo (sondeo cada segundo con cursor hasta que la API indica `finished`), pasos, intentos y cancelación con confirmación. El nivel del log se marca con un filete de color, no con el color del texto. Cifras con `Intl.NumberFormat` del idioma.
- [x] Verificado a 375, 768, 1024 y 1440 px en claro y grafito contra la pila real (worker incluido), sin desborde: las tarjetas del detalle de run van en una rejilla `minmax(0, 1fr)` (con `.sx-stack` la tabla de pasos estiraba la página a 527 px en 375). Contraste medido del log: 11,3:1 y 4,56:1 (hora y paso, claro), 16,2:1 y 8,1:1 (grafito); tokens 12,1:1 y 15,6:1. axe sin incumplimientos en `/pipelines`, `/pipelines/new` y `/runs`. E2E: pipeline delta → activar → ejecutar ahora → worker → inicialización confirmada.

**Iteración 8** (21.11.2026, plan 09 «Lo construido en la iteración 8»)
- [x] «Panel» (`/dashboard`, permiso `dashboard:read`) abre la sección Operación del carril y «Alertas» sale de «próximamente». Panel: filtros de entorno y etiqueta, KPIs de 24 h, runs en curso con paso y progreso, línea de tiempo de 24 h (barra por run con su estado, enlace al run), fallos recientes, frescura con semáforo, workers y sistemas SAP; se refresca cada 5 s.
- [x] Alertas (`/alerts`): pestañas Abiertas, Historial (filtro por estado), Reglas (alta y edición con los parámetros de cada tipo, ámbito global, pipeline o etiqueta) y Canales (solo con `alert:manage`: correo, Teams o webhook; prueba de envío; suscripciones por regla y gravedad mínima). Reconocer y cerrar con `alert:ack`. Campana en la barra superior con el número de alertas abiertas (cada 30 s).
- [x] El carril volvía a necesitar scroll a 1366 × 830 con la nueva tecla (681 px de contenido en 658): el relleno de las teclas pasa a 5/4 px y la separación de grupos a 4 px; ahora cabe justo (658/658), también con la tecla «Ajustes» y la compactación por altura de la It6 (medido a 1366 × 830, 1366 × 881, 1440 × 900 y 1920 × 1080). «Filas leídas» va en notación compacta («10 mil», «1,3 M»): con la cifra completa, «10.000» se partía en dos líneas cuando la rejilla de KPIs daba columnas estrechas.
- [x] Verificado a 375, 768, 1024 y 1440 px en claro y grafito contra la pila real (alertas y línea de tiempo simuladas con `page.route`), sin desborde. Contraste medido: celdas y cabeceras 4,9:1 (claro) y 7,8:1 (grafito), pastillas ≥ 5,05:1, número de la campana 6,25:1 y 5,23:1, barras de la línea de tiempo contra su pista ≥ 4,67:1. axe sin incumplimientos en `/dashboard` y `/alerts`.

**Por cada módulo**
- [ ] Pantallas descritas en su plan, usando solo `shared/ui`.
- [ ] Pruebas de componentes y E2E del flujo principal.

## Criterios de aceptación

- Cambiar la plantilla/tema no requiere modificar archivos de `features/`.
- Un usuario nuevo completa "conectar SAP → crear origen → crear destino → programar pipeline" sin documentación externa, en < 15 minutos (prueba de usabilidad con 2–3 usuarios).
- Lighthouse accesibilidad ≥ 90 en las pantallas principales.
