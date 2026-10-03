-- =============================================================================
-- Consultas para la demo, sobre el core (PostgreSQL de infra/payments)
-- =============================================================================
-- Para pgAdmin conectado a la base `acquiring`. Hora de negocio: Lima.
-- Todas son de solo lectura.
-- =============================================================================

-- ── 1. El feed en vivo: los últimos pagos, de cualquier canal ────────────────
-- Haz un pago (el Core2, la tienda o el QR) y refresca: aparece arriba.
SELECT to_char(pi.created_at AT TIME ZONE 'America/Lima', 'HH24:MI:SS') AS hora,
       m.trade_name                       AS comercio,
       COALESCE(pi.channel::text, '—')    AS canal,
       pi.status::text                    AS intencion,
       c.status::text                     AS cobro,
       c.response_code                    AS codigo,
       round(pi.amount_minor / 100.0, 2)            AS importe,
       pi.currency_code                   AS moneda,
       pi.intent_reference
FROM acquiring.payment_intents pi
JOIN acquiring.merchants m ON m.id = pi.merchant_id
LEFT JOIN LATERAL (
    SELECT * FROM acquiring.charges c
    WHERE c.payment_intent_id = pi.id ORDER BY c.id DESC LIMIT 1
) c ON TRUE
ORDER BY pi.created_at DESC
LIMIT 20;

-- ── 2. Tu última compra en Mercado Andino, con su carrito ────────────────────
SELECT pi.intent_reference,
       pi.status::text                     AS estado,
       pi.channel::text                    AS canal,
       round(pi.amount_minor / 100.0, 2)             AS total,
       item ->> 'name'                     AS producto,
       (item ->> 'quantity')::int          AS cantidad,
       round((item ->> 'unit_price_minor')::int / 100.0, 2) AS precio
FROM acquiring.payment_intents pi
JOIN acquiring.merchants m ON m.id = pi.merchant_id
CROSS JOIN LATERAL jsonb_array_elements(pi.line_items) AS item
WHERE m.trade_name = 'Mercado Andino'
  AND pi.id = (SELECT max(pi2.id) FROM acquiring.payment_intents pi2 WHERE pi2.merchant_id = m.id);

-- ── 3. Hoy, por canal: cuánto, cuánto se aprueba ─────────────────────────────
SELECT c.channel::text                                             AS canal,
       count(*)                                                    AS intentos,
       count(*) FILTER (WHERE c.response_code = '00')              AS aprobados,
       round(100.0 * count(*) FILTER (WHERE c.response_code = '00') / count(*), 1) AS tasa_aprobacion_pct,
       round(sum(c.amount_captured_minor) / 100.0, 2)                        AS importe_capturado
FROM acquiring.charges c
WHERE (c.created_at AT TIME ZONE 'America/Lima')::date = (now() AT TIME ZONE 'America/Lima')::date
GROUP BY c.channel
ORDER BY intentos DESC;

-- ── 4. Por qué se rechaza: los códigos ISO 8583 (DE 39) ───────────────────────
SELECT c.response_code AS codigo,
       CASE c.response_code
            WHEN '05' THEN 'No honrar (rechazo genérico del emisor)'
            WHEN '51' THEN 'Fondos insuficientes'
            WHEN '54' THEN 'Tarjeta vencida'
            WHEN '91' THEN 'Emisor no disponible'
       END             AS significado,
       c.channel::text AS canal,
       count(*)        AS cobros
FROM acquiring.charges c
WHERE c.response_code <> '00'
GROUP BY 1, 2, 3
ORDER BY cobros DESC;

-- ── 5. El libro mayor: lo que gana el adquirente, por comercio ───────────────
-- net = gross - fee lo garantiza una constraint: la columna "cuadra" lo enseña.
SELECT m.trade_name                       AS comercio,
       bt.currency_code                   AS moneda,
       round(sum(bt.gross_minor) / 100.0, 2)        AS bruto,
       round(sum(bt.fee_minor) / 100.0, 2)        AS comision_mdr,
       round(sum(bt.net_minor) / 100.0, 2)        AS neto_a_liquidar,
       bool_and(bt.net_minor = bt.gross_minor - bt.fee_minor) AS cuadra
FROM acquiring.balance_transactions bt
JOIN acquiring.merchants m ON m.id = bt.merchant_id
GROUP BY 1, 2
ORDER BY bruto DESC;

-- ── 6. Devoluciones: el cobro de ayer que hoy cambia ─────────────────────────
-- En el core el cobro sigue CAPTURED; la vista del lago lo ve REFUNDED si es total.
SELECT r.refund_reference,
       c.charge_reference,
       m.trade_name                       AS comercio,
       round(r.amount_minor / 100.0, 2)             AS devuelto,
       round(c.amount_captured_minor / 100.0, 2)    AS capturado,
       r.reason                           AS motivo,
       v.status                           AS estado_en_el_lago,
       to_char(c.created_at AT TIME ZONE 'America/Lima', 'DD/MM HH24:MI') AS cobrado,
       to_char(r.created_at AT TIME ZONE 'America/Lima', 'DD/MM HH24:MI') AS devuelto_el
FROM acquiring.refunds r
JOIN acquiring.charges c   ON c.id = r.charge_id
JOIN acquiring.merchants m ON m.id = r.merchant_id
JOIN acquiring.v_transactions_for_ingestion v ON v.transaction_id = c.charge_reference
ORDER BY r.created_at DESC;

-- ── 7. La vida de un pago: sus eventos, en orden ─────────────────────────────
-- Cambia la referencia por la de un pago con 3DS o con un reintento.
SELECT to_char(e.occurred_at AT TIME ZONE 'America/Lima', 'HH24:MI:SS.MS') AS momento,
       e.event_type,
       e.payload
FROM acquiring.webhook_events e
WHERE e.object_id = (SELECT intent_reference FROM acquiring.payment_intents ORDER BY id DESC LIMIT 1)
ORDER BY e.id;

-- ── 8. 3-D Secure: quién se autenticó y quién no ─────────────────────────────
SELECT pi.intent_reference,
       m.trade_name                AS comercio,
       pi.status::text             AS estado,
       pi.last_error_code          AS ultimo_error,
       round(pi.amount_minor / 100.0, 2)     AS importe
FROM acquiring.payment_intents pi
JOIN acquiring.merchants m ON m.id = pi.merchant_id
JOIN acquiring.payment_methods pm ON pm.id = pi.payment_method_id
WHERE pm.processor_token = 'pm_card_threeDSecure2Required'
ORDER BY pi.id DESC
LIMIT 15;

-- ── 9. Card testing: tiendas online con muchos rechazos en 15 minutos ────────
SELECT m.trade_name                AS comercio,
       m.mcc,
       m.risk_level::text          AS riesgo,
       count(*)                    AS intentos,
       count(*) FILTER (WHERE c.response_code <> '00') AS rechazados,
       round(100.0 * count(*) FILTER (WHERE c.response_code <> '00') / count(*), 0) AS pct_rechazo,
       round(avg(c.amount_minor) / 100.0, 2) AS ticket_medio
FROM acquiring.charges c
JOIN acquiring.merchants m ON m.id = c.merchant_id
WHERE c.channel = 'CARD_NOT_PRESENT'
  AND c.created_at > now() - interval '15 minutes'
GROUP BY 1, 2, 3
HAVING count(*) >= 4
ORDER BY pct_rechazo DESC;

-- ── 10. Lo que no se cobró: QR y carritos abandonados ────────────────────────
SELECT m.trade_name                           AS comercio,
       COALESCE(t.terminal_id, 'online')      AS terminal,
       count(*) FILTER (WHERE pi.status = 'CANCELED')             AS cancelados,
       count(*) FILTER (WHERE pi.status = 'REQUIRES_CONFIRMATION') AS qr_sin_pagar,
       count(*) FILTER (WHERE pi.status = 'SUCCEEDED')            AS cobrados
FROM acquiring.payment_intents pi
JOIN acquiring.merchants m ON m.id = pi.merchant_id
LEFT JOIN acquiring.terminals t ON t.id = pi.terminal_id
GROUP BY 1, 2
ORDER BY 3 DESC, 4 DESC;

-- ── 11. Lo que se lleva DMS: la vista del contrato, una fila por cobro ───────
SELECT * FROM acquiring.v_transactions_for_ingestion
ORDER BY created_at DESC
LIMIT 10;

-- ── 12. El CDC por dentro: el slot de replicación de DMS ─────────────────────
-- activo = DMS está leyendo. retenido = WAL que la base guarda para él.
SELECT slot_name, plugin, active AS activo,
       pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) AS retenido,
       current_setting('max_slot_wal_keep_size') AS tope
FROM pg_replication_slots;
