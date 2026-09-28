-- =============================================================================
-- 004 — La tabla instantanea que replica DMS
-- =============================================================================
-- Existe por una limitacion de AWS DMS descubierta desplegando el 2026-09-28:
--
--   **DMS full-load NO puede replicar una vista de PostgreSQL.** La tarea muere
--   con `No tables were found at task initialization` antes de consultar nada.
--   Tampoco sirve una vista materializada. Solo lee tablas de verdad.
--
-- Comprobado aislando los tres casos contra Aurora PostgreSQL 16.10:
--
--   | seleccion                      | resultado          |
--   |--------------------------------|--------------------|
--   | v_transactions_for_ingestion   | failed, 0 tablas   |
--   | mv_... (vista materializada)   | failed, 0 tablas   |
--   | charges (tabla)                | Table completed    |
--   | tbl_transactions_for_ingestion | Table completed    |
--
-- La vista se conserva: sigue siendo el contrato legible, lo que documenta que
-- se expone, y lo que se consulta a mano. Esta tabla es su materializacion para
-- DMS, y nada mas.
--
-- El precio es que hay que refrescarla antes de cada carga. Es el mismo precio
-- que ya se pagaba —el full load es bajo demanda, no continuo— asi que no
-- cambia la naturaleza del pipeline: sigue siendo batch para la historia y
-- streaming para el presente (ADR-019).

SET search_path TO acquiring, public;

-- LIKE + INSERT y no CREATE TABLE AS: asi la tabla existe con el tipo correcto
-- de cada columna aunque la vista este vacia el dia del despliegue.
DROP TABLE IF EXISTS tbl_transactions_for_ingestion;

CREATE TABLE tbl_transactions_for_ingestion AS
    SELECT * FROM v_transactions_for_ingestion WITH NO DATA;

COMMENT ON TABLE tbl_transactions_for_ingestion IS
    'Materializacion de v_transactions_for_ingestion para DMS. DMS no lee vistas. Refrescar con refresh_ingestion_snapshot() antes de cada full load.';

-- El refresco. Se llama antes de arrancar la tarea DMS.
CREATE OR REPLACE FUNCTION refresh_ingestion_snapshot()
RETURNS bigint
LANGUAGE plpgsql
AS $$
DECLARE
    filas bigint;
BEGIN
    -- TRUNCATE y no DELETE: no hay que conservar nada, y evita inflar la tabla
    -- con tuplas muertas en cada refresco.
    TRUNCATE tbl_transactions_for_ingestion;
    INSERT INTO tbl_transactions_for_ingestion
        SELECT * FROM v_transactions_for_ingestion;
    GET DIAGNOSTICS filas = ROW_COUNT;
    RETURN filas;
END;
$$;

COMMENT ON FUNCTION refresh_ingestion_snapshot() IS
    'Rellena la tabla que replica DMS desde la vista. Devuelve las filas copiadas.';

SELECT 'Snapshot de ingesta listo' AS resultado,
       refresh_ingestion_snapshot()  AS filas_copiadas;
