# ADR 0001 · Monolito modular Python/FastAPI + SPA React

**Estado:** Aceptado · **Fecha:** 18.11.2026

## Contexto
La plataforma tiene módulos con fronteras claras (identidad, auditoría, conexiones SAP, orígenes, motor, destinos, orquestación, monitoreo) pero un único equipo y un único despliegue. El trabajo pesado es de datos (Arrow, conectores Snowflake/SQL Server).

## Decisión
- Backend: **monolito modular** en Python 3.14 + FastAPI, con capas `api/ application/ domain/ infrastructure/` por módulo y tres procesos (`api`, `scheduler`, `worker`) desde la misma base de código.
- Frontend: **SPA React + TypeScript (Vite)** separada, cuyo único contrato con el backend es el OpenAPI (`/api/v1`). Los tipos se generan (`openapi-typescript`) y CI falla si el contrato versionado no coincide.

## Alternativas
- Microservicios: coste operativo alto sin beneficio a esta escala.
- Backend en .NET/Java: buen rendimiento, pero peor ecosistema de datos para Arrow/Parquet/Snowflake.
- Render en servidor (Jinja/HTMX): menos adecuado para el diseñador visual de pipelines.

## Consecuencias
- Despliegue y pruebas simples; las fronteras se protegen con convenciones y revisión (sin acceso a tablas de otro módulo).
- Si un módulo necesita escalar aparte (p. ej. streaming), puede extraerse gracias a las interfaces por puertos.
