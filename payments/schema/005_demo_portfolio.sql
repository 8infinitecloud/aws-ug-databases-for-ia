-- =============================================================================
-- 005 — La cartera de comercios del core (doce, con Mercado Andino)
-- =============================================================================
-- Con un solo comercio (Smart Coffee) los labs no tienen nada que comparar: no
-- hay MCC distintos, ni niveles de riesgo, ni tarifas, ni canales. Esta es la
-- cartera que mueve el generador de trafico (scripts/generate_traffic.py).
--
-- La eleccion no es decorativa. Cada comercio esta aqui por una pregunta que
-- los labs pueden hacerse:
--
--   MCC 6051 (casa de cambio) y 5816 (bienes digitales) son HIGH: son los que
--   el corpus de compliance del Lab 3 manda vigilar, y los que mas dispara el
--   scoring del Lab 4.
--   Online contra mostrador: la tasa de declinacion y el 3DS solo existen en el
--   canal online, y el embudo de abandono tambien.
--   Un comercio en PENDING_KYC: la base le impide cobrar (merchants_charges_need_kyc)
--   y el generador lo intenta igual, para que el 409 quede registrado.
--   Hotel Miraflores cobra en mostrador Y online: el mismo comercio en dos
--   canales.
--
-- Los nombres son distintos de los de la cartera legacy (merchants.csv del
-- Lab 1) a proposito: son dos carteras, y el Lab 2 las distingue por `source`.
--
-- Los terminales son VIRTUALES salvo TID00001, que es el Core2 real. Su serial
-- empieza por `sim-` para que en el lago se distinga sin ambiguedad que
-- dispositivo es fisico; es tambien la raiz de su topic en IoT Core.
--
-- DEMO: comercios, RUC y cuentas son ficticios. Idempotente.
-- =============================================================================

BEGIN;
SET search_path TO acquiring, public;

INSERT INTO merchants (
    merchant_id, legal_name, trade_name, tax_id, mcc, country_code,
    default_currency, status, risk_level, kyc_completed_at,
    mdr_bps, fixed_fee_minor, charges_enabled, payouts_enabled,
    payout_delay_days, metadata
) VALUES
    -- ── Mostrador ────────────────────────────────────────────────────────────
    ('MID000000020001', 'Picanteria Arequipa S.A.C.', 'Picanteria Arequipa', '20600000001',
     '5812', 'PE', 'PEN', 'ACTIVE', 'LOW',    NOW() - INTERVAL '400 days',
     299, 30, TRUE, TRUE, 2, '{"canal":"POS"}'),
    ('MID000000020002', 'Minimarket San Isidro E.I.R.L.', 'Minimarket San Isidro', '20600000002',
     '5411', 'PE', 'PEN', 'ACTIVE', 'LOW',    NOW() - INTERVAL '700 days',
     199, 20, TRUE, TRUE, 1, '{"canal":"POS"}'),
    ('MID000000020003', 'Farmacia Vida S.A.C.', 'Farmacia Vida', '20600000003',
     '5912', 'PE', 'PEN', 'ACTIVE', 'LOW',    NOW() - INTERVAL '300 days',
     249, 20, TRUE, TRUE, 2, '{"canal":"POS"}'),
    ('MID000000020004', 'Taxi Seguro Lima S.R.L.', 'Taxi Seguro Lima', '20600000004',
     '4121', 'PE', 'PEN', 'ACTIVE', 'MEDIUM', NOW() - INTERVAL '120 days',
     319, 30, TRUE, TRUE, 2, '{"canal":"POS"}'),
    ('MID000000020005', 'Casa de Cambio Sol S.A.C.', 'Casa de Cambio Sol', '20600000005',
     '6051', 'PE', 'USD', 'ACTIVE', 'HIGH',   NOW() - INTERVAL '60 days',
     450, 50, TRUE, TRUE, 5, '{"canal":"POS"}'),
    -- ── Mostrador y online ───────────────────────────────────────────────────
    ('MID000000020006', 'Hotel Miraflores Suites S.A.', 'Hotel Miraflores Suites', '20600000006',
     '7011', 'PE', 'USD', 'ACTIVE', 'MEDIUM', NOW() - INTERVAL '900 days',
     320, 30, TRUE, TRUE, 3, '{"canal":"POS+ONLINE"}'),
    -- ── Online ───────────────────────────────────────────────────────────────
    ('MID000000020007', 'TecnoMarket Online S.A.C.', 'TecnoMarket Online', '20600000007',
     '5732', 'PE', 'PEN', 'ACTIVE', 'MEDIUM', NOW() - INTERVAL '250 days',
     349, 30, TRUE, TRUE, 3, '{"canal":"ONLINE"}'),
    ('MID000000020008', 'Moda Urbana Online E.I.R.L.', 'Moda Urbana Online', '20600000008',
     '5691', 'PE', 'PEN', 'ACTIVE', 'LOW',    NOW() - INTERVAL '180 days',
     349, 30, TRUE, TRUE, 3, '{"canal":"ONLINE"}'),
    ('MID000000020009', 'Rutas del Inca Viajes S.A.C.', 'Rutas del Inca Viajes', '20600000009',
     '4722', 'PE', 'USD', 'ACTIVE', 'MEDIUM', NOW() - INTERVAL '365 days',
     399, 30, TRUE, TRUE, 7, '{"canal":"ONLINE"}'),
    ('MID000000020010', 'PixelPlay Store S.R.L.', 'PixelPlay Store', '20600000010',
     '5816', 'PE', 'USD', 'ACTIVE', 'HIGH',   NOW() - INTERVAL '45 days',
     499, 30, TRUE, TRUE, 7, '{"canal":"ONLINE"}'),
    -- La tienda de store/: un supermercado que vende online y en caja. Es el
    -- comercio que usa el ponente durante la demo.
    ('MID000000020012', 'Mercado Andino S.A.C.', 'Mercado Andino', '20600000012',
     '5411', 'PE', 'PEN', 'ACTIVE', 'LOW',    NOW() - INTERVAL '500 days',
     249, 20, TRUE, TRUE, 2, '{"canal":"POS+ONLINE","app":"store/"}')
ON CONFLICT (merchant_id) DO NOTHING;

-- Sin KYC: la constraint merchants_charges_need_kyc impide charges_enabled.
INSERT INTO merchants (
    merchant_id, legal_name, trade_name, tax_id, mcc, country_code,
    default_currency, status, risk_level, charges_enabled, metadata
) VALUES
    ('MID000000020011', 'Nueva Tienda Online S.A.C.', 'Nueva Tienda Online', '20600000011',
     '5999', 'PE', 'PEN', 'PENDING_KYC', 'MEDIUM', FALSE, '{"canal":"ONLINE"}')
ON CONFLICT (merchant_id) DO NOTHING;

-- ── Terminales virtuales ─────────────────────────────────────────────────────
INSERT INTO terminals (merchant_id, terminal_id, label, device_serial, device_model,
                       firmware_version, status)
SELECT m.id, t.tid, t.label, t.serial, 'Terminal simulado', 'sim 1.0.0', 'ACTIVE'
FROM (VALUES
    ('MID000000020001', 'TID00002', 'Caja 1',        'sim-tid00002'),
    ('MID000000020001', 'TID00003', 'Caja 2',        'sim-tid00003'),
    ('MID000000020002', 'TID00004', 'Caja principal','sim-tid00004'),
    ('MID000000020003', 'TID00005', 'Mostrador',     'sim-tid00005'),
    ('MID000000020004', 'TID00006', 'Unidad 14',     'sim-tid00006'),
    ('MID000000020004', 'TID00007', 'Unidad 22',     'sim-tid00007'),
    ('MID000000020005', 'TID00008', 'Ventanilla',    'sim-tid00008'),
    ('MID000000020006', 'TID00009', 'Recepcion',     'sim-tid00009'),
    -- La caja de Mercado Andino: el QR de "pagar en caja" de store/ sale de aqui.
    ('MID000000020012', 'TID00010', 'Caja web',      'sim-tid00010')
) AS t(mid, tid, label, serial)
JOIN merchants m ON m.merchant_id = t.mid
ON CONFLICT (terminal_id) DO NOTHING;

COMMIT;

SELECT
    'Cartera aplicada'                                        AS resultado,
    (SELECT count(*) FROM merchants)                          AS comercios,
    (SELECT count(*) FROM merchants WHERE charges_enabled)    AS con_cobros,
    (SELECT count(*) FROM terminals)                          AS terminales;
