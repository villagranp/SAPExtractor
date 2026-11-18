# ADR 0005 · Cifrado envelope de secretos

**Estado:** Aceptado · **Fecha:** 18.11.2026

## Contexto
La plataforma guarda credenciales de SAP, SQL Server, Snowflake y Entra ID.

## Decisión
AES-256-GCM con una clave de datos por secreto, envuelta con la clave maestra (`SX_MASTER_KEY`, o Azure Key Vault en el futuro). Formato `sx1.<key_id>.<dek_envuelta>.<datos>`; el contexto (`aad`) liga cada secreto a su registro. Implementado en `backend/src/sapextract/core/security/crypto.py` con rotación por re-envoltura.

## Consecuencias
- Rotar la clave maestra no exige descifrar todos los datos, solo re-envolver las claves.
- Perder la clave maestra impide descifrar credenciales: custodia y respaldo obligatorios (plan 11).
