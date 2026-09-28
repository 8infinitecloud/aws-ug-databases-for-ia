-- =============================================================================
-- Seed — Smart Coffee como comercio afiliado
-- =============================================================================
-- Datos minimos para que la demo arranque. Idempotente: ON CONFLICT DO NOTHING.
--
-- DEMO: la tarjeta y el token son ficticios. Ver payments/README.md.
-- =============================================================================

BEGIN;
SET search_path TO acquiring, public;

-- ── El comercio ──────────────────────────────────────────────────────────────
-- MCC 5812 = Eating Places and Restaurants (ISO 18245).
-- MDR 2.99% + S/0.30 por transaccion: tarifa tipica de adquirencia en la region.
-- risk_level MEDIUM: una cafeteria no es alto riesgo, pero tampoco lo mas bajo.
INSERT INTO merchants (
    merchant_id, legal_name, trade_name, tax_id, mcc,
    country_code, default_currency, status, risk_level, kyc_completed_at,
    mdr_bps, fixed_fee_minor, charges_enabled, payouts_enabled,
    payout_schedule, payout_delay_days, settlement_account, metadata
) VALUES (
    'MID000000012345',
    'Smart Coffee Peru S.A.C.',
    'Smart Coffee',
    '20605551234',
    '5812',
    'PE', 'PEN', 'ACTIVE', 'MEDIUM', NOW() - INTERVAL '90 days',
    299, 30, TRUE, TRUE,
    'DAILY', 2, '00219912345678901234',
    '{"canal_afiliacion":"demo","sucursal":"San Juan de Miraflores"}'::jsonb
) ON CONFLICT (merchant_id) DO NOTHING;

-- ── El terminal ──────────────────────────────────────────────────────────────
-- device_serial es el serial real del elemento seguro ATECC608 del M5Stack de
-- la demo. Es tambien el clientId con el que ese dispositivo se autentica en
-- AWS IoT Core, asi que el mismo valor une la base de datos y el broker MQTT.
INSERT INTO terminals (
    merchant_id, terminal_id, label, device_serial, device_model,
    firmware_version, status
)
SELECT m.id, 'TID00001', 'POS-01 Barra',
       '012370911fd7211101',
       'M5Stack Core2 for AWS IoT Kit',
       'smart-coffee-pos 0.1.0',
       'ACTIVE'
FROM merchants m WHERE m.merchant_id = 'MID000000012345'
ON CONFLICT (terminal_id) DO NOTHING;

-- ── El instrumento de pago de demo ───────────────────────────────────────────
-- DEMO: no es una tarjeta real. 4242 es el numero de prueba universal de las
-- pasarelas de pago, y aqui solo se guardan los 4 ultimos digitos y un token
-- ficticio. El PAN completo no existe en ninguna parte de este sistema.
INSERT INTO payment_methods (
    merchant_id, method_type, brand, last_four, exp_month, exp_year,
    processor_token, fingerprint, status
)
SELECT m.id, 'CARD', 'VISA', '4242', 12, 2030,
       'demo_card_4242',
       'demo_fp_4242_do_not_use_in_production',
       'ACTIVE'
FROM merchants m WHERE m.merchant_id = 'MID000000012345'
ON CONFLICT (processor_token) DO NOTHING;

-- ── Catalogo de productos ────────────────────────────────────────────────────
-- Vive en el POS, no en el adquirente: a nosotros no nos importa QUE se vende,
-- solo cuanto se cobra. Se deja aqui como referencia para que el firmware y la
-- web de pago muestren los mismos precios, en una tabla fuera del schema
-- transaccional para que quede claro que no forma parte del modelo de pagos.
CREATE TABLE IF NOT EXISTS demo_catalog (
    sku              VARCHAR(8)   PRIMARY KEY,
    name             VARCHAR(64)  NOT NULL,
    category         VARCHAR(32)  NOT NULL,
    unit_price_minor INTEGER      NOT NULL CHECK (unit_price_minor > 0)
);

COMMENT ON TABLE demo_catalog IS
    'Catalogo del comercio, NO forma parte del modelo del adquirente. Solo para que el POS y la web muestren los mismos precios';

INSERT INTO demo_catalog (sku, name, category, unit_price_minor) VALUES
    ('P001', 'Americano',   'BEBIDA_CALIENTE',  850),
    ('P002', 'Latte',       'BEBIDA_CALIENTE', 1000),
    ('P003', 'Cappuccino',  'BEBIDA_CALIENTE', 1100),
    ('P004', 'Croissant',   'PANADERIA',        700),
    ('P005', 'Cold Brew',   'BEBIDA_FRIA',      950),
    ('P006', 'Alfajor',     'PANADERIA',        450)
ON CONFLICT (sku) DO NOTHING;

COMMIT;

-- ── Comprobacion ─────────────────────────────────────────────────────────────
-- Sin meta-comandos de psql (\echo y compania): solo los entiende psql, y este
-- fichero tambien lo cargan clientes programaticos —una tarea de Fargate, un
-- Lambda, el CI—. Un `\echo` los rompe con un error de sintaxis que no dice de
-- que va. La etiqueta viaja como una columna mas, que se ve igual en psql.
SELECT
    'Seed aplicado'                        AS resultado,
    (SELECT count(*) FROM merchants)       AS comercios,
    (SELECT count(*) FROM terminals)       AS terminales,
    (SELECT count(*) FROM payment_methods) AS instrumentos,
    (SELECT count(*) FROM demo_catalog)    AS productos;
