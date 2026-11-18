# ADR 0004 · Sesiones de servidor con cookie httpOnly (patrón BFF)

**Estado:** Aceptado · **Fecha:** 18.11.2026

## Contexto
Hay login local (admin de emergencia) y SSO con Entra ID. Guardar tokens en el navegador (localStorage) los expone a XSS.

## Decisión
El backend gestiona el flujo OIDC (authorization code + PKCE) y crea una **sesión de servidor**; el navegador solo recibe la cookie `sx_session` (`httpOnly`, `Secure`, `SameSite=Lax`) y una cookie CSRF que el cliente reenvía en `X-CSRF-Token` (ya soportado por el cliente API del frontend).

## Consecuencias
- Revocación inmediata de sesiones (baja de usuario) y sin tokens de Entra en el navegador.
- Requiere protección CSRF y que frontend y API compartan origen (proxy Nginx), como está previsto.
