# ADR 0007 · Estilo visual «táctil» propio (iteración 1b)

**Estado:** Aceptado · **Fecha:** 19.11.2026

## Contexto
El plan 10 preveía integrar una plantilla corporativa en `shared/ui`. Se eligió como referencia visual la plantilla comercial **Atmosphere** (React 19, Vite, Tailwind CSS v4; demo pública en `atmosphere.jawad.work/demo`): un único material mate iluminado desde un lado, botones como teclas que se hunden, campos y estados en «pozos», sin bordes, tema claro y grafito.

La plantilla es de pago y no se ha comprado. Copiar su CSS, su JavaScript o sus recursos desde la demo no es una opción.

## Decisión
- **Diseño propio con el mismo lenguaje visual**, escrito desde cero sobre el design system existente (`frontend/src/shared/ui`). No se usa código, CSS, imágenes ni valores de la plantilla. Si en el futuro se compra la licencia, sus componentes se podrán portar detrás de la misma API de `shared/ui`.
- **Material** (`shared/theme/tokens.css`): suelo, tarjeta en relieve (`--sx-shadow-raised`), tecla con canto y recorrido (`--sx-shadow-key`, `--sx-key-travel`) y pozo (`--sx-shadow-well`). Luz desde arriba a la izquierda.
- **Temas claro y grafito** con `light-dark()` en un solo bloque de tokens. `data-theme` en `<html>` fuerza uno; sin él se sigue al sistema. **Densidad compacta** con `data-density="compact"`. Se eligen en *Mi perfil* (y el tema también desde la cabecera), se guardan en el navegador y `public/theme-init.js` los aplica antes del primer pintado (archivo externo: la CSP no admite scripts en línea).
- **Accesibilidad**: todos los pares de texto y fondo superan 4,5:1 en ambos temas; el job E2E pasa axe (WCAG 2.1 AA) en las pantallas principales en claro y en grafito.
- **Paleta** (actualizada el 20.11.2026; sustituye al acento naranja con grises neutros del 19.11.2026): **Mega Lucario**, construida en OKLCH y registrada en `CLAUDE.md`. Cuatro colores con un solo papel cada uno:
  - *Azul de aura* (`#1e65aa` en claro, `#67aae9` en grafito): acento para la acción primaria, el estado activo y la información.
  - *Negro de pelaje* (tono 266): tinta del tema claro, material del grafito y la tecla de usuario del carril en grafito.
  - *Crema del pecho* (tono 92): leyenda de las teclas azules en claro (4,7:1), tinta del grafito, tecla de usuario del carril en claro y color del aviso. La tecla de usuario se invierte como la tinta (20.11.2026): en claro, crema con el icono en negro de pelaje (12,6:1), porque la tecla negra contrastaba demasiado con el carril pálido; su croma queda por debajo del fondo de aviso para no leerse como alerta.
  - *Rojo de ojos* (tono 24–26): solo peligro y error.

  Los neutros llevan un leve tinte azul (claro) o de pelaje (grafito); el éxito es un verde azulado afinado a la paleta. El logotipo (20.11.2026; sustituye al monograma «SX») es una ilustración propia en PNG con transparencia: se sirve como `BrandMark` de `shared/ui` (WebP 64/128/192 px en `shared/ui/assets/`) sobre una tecla neutra en el carril y el login, y como favicon (`public/favicon-32.png`, `favicon-192.png`, `apple-touch-icon.png`). Lleva su propio azul, por eso la tecla no usa el acento. Cambiar la paleta solo toca `tokens.css`, recalculando los contrastes.
- **Dependencias**: iconos Tabler (`@tabler/icons-react`, MIT), reexportados desde `shared/ui` (ESLint impide importarlos directamente en `features/` y `app/`), y la fuente Onest autoalojada (`@fontsource-variable/onest`, OFL), compatible con `font-src 'self'`.
- **Layout**: carril vertical de teclas con icono y etiqueta (las iteraciones futuras se ven atenuadas con su número), cabecera con la ruta, el entorno, el cambio de tema y la sesión. En pantallas estrechas el carril pasa a una fila superior desplazable.

## Alternativas descartadas
- **Comprar e integrar Atmosphere**: posible más adelante; habría que añadir Tailwind v4 y adaptar su router y sus datos de demo.
- **Copiar el CSS de la demo**: descartado por licencia.
- **Tailwind CSS**: no hace falta para un design system pequeño basado en tokens; mantiene `shared/ui` independiente de la herramienta.

## Consecuencias
- Las pantallas de `features/` no cambiaron (salvo el inicio, rediseñado, y dos detalles: el punto duplicado de una píldora y la tarjeta de apariencia del perfil): el cambio de estilo entra por `shared/ui` y `app/layout`.
- Nuevos componentes de `shared/ui`: `Metric`, `Meter`, `SegmentedControl` y los iconos; `PageHeader` admite `eyebrow`.
- Si se adopta la marca de Grupo AG, se sustituyen los tokens de marca y los archivos del logotipo (mismos nombres y tamaños).
