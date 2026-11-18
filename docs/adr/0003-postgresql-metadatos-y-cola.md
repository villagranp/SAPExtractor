# ADR 0003 · PostgreSQL como base de metadatos y cola de trabajos

**Estado:** Aceptado (sujeto a la decisión abierta D3) · **Fecha:** 18.11.2026

## Contexto
Se necesita guardar configuración, estado de deltas, auditoría e historial, además de una cola de trabajos fiable y la elección de un líder para el scheduler.

## Decisión
PostgreSQL 16 en esquema `sx`, migraciones con Alembic. Cola con `SELECT … FOR UPDATE SKIP LOCKED`, avisos con `LISTEN/NOTIFY` y liderazgo del scheduler con `pg_try_advisory_lock` (ya implementado en iteración 0).

## Alternativas
- Redis/Celery: más infraestructura y la cola quedaría fuera de la transacción del estado de delta.
- SQL Server como metadatos: posible, pero sin `LISTEN/NOTIFY` ni advisory locks equivalentes de forma tan directa; requeriría rediseñar cola y liderazgo.

## Consecuencias
- Un único servicio de infraestructura además de la app. Los delta tokens viven aquí: la BD es crítica (backups con PITR, plan 11).
