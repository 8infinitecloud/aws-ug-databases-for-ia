-- =============================================================================
-- 004 — Las tablas que replica DMS, preparadas para el CDC
-- =============================================================================
-- DMS lee el WAL decodificado (test_decoding). Lo que el WAL lleva de cada cambio
-- depende de la REPLICA IDENTITY de la tabla, y el valor por defecto se queda
-- corto para un lago:
--
--   DEFAULT  un DELETE solo lleva la clave primaria; un UPDATE no lleva las
--            columnas grandes que no cambiaron (TOAST), como un line_items JSONB
--            largo. En raw/ esas filas llegarian con huecos.
--   FULL     cada cambio lleva la fila entera. Mas WAL, pero en raw/ cada delta
--            es una fila completa que Glue puede usar sin reconstruirla.
--
-- Son las mismas tablas que incluye la tarea de DMS (infra/lab1/dms.tf,
-- local.replicated_tables). Las que no se replican —claves de API, secretos de
-- webhooks, tokens de pago— no necesitan nada de esto.
--
-- Antes este fichero creaba tbl_transactions_for_ingestion, una tabla espejo de
-- la vista, porque DMS no puede replicar vistas (ADR-019). Con CDC sobre las
-- tablas base ya no hace falta: se borra si existe de una instalacion anterior.
--
-- Idempotente.
-- =============================================================================

BEGIN;
SET search_path TO acquiring, public;

ALTER TABLE merchants            REPLICA IDENTITY FULL;
ALTER TABLE terminals            REPLICA IDENTITY FULL;
ALTER TABLE payment_intents      REPLICA IDENTITY FULL;
ALTER TABLE charges              REPLICA IDENTITY FULL;
ALTER TABLE balance_transactions REPLICA IDENTITY FULL;
ALTER TABLE refunds              REPLICA IDENTITY FULL;
ALTER TABLE disputes             REPLICA IDENTITY FULL;
ALTER TABLE payouts              REPLICA IDENTITY FULL;
ALTER TABLE webhook_events       REPLICA IDENTITY FULL;

-- La tabla espejo de la version anterior.
DROP FUNCTION IF EXISTS refresh_ingestion_snapshot();
DROP TABLE IF EXISTS tbl_transactions_for_ingestion;

COMMIT;

SELECT
    'Replicacion preparada' AS resultado,
    count(*) FILTER (WHERE c.relreplident = 'f') AS tablas_full
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE n.nspname = 'acquiring' AND c.relkind = 'r';
