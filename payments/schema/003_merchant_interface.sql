-- =============================================================================
-- 003 — Interfaz con el comercio: notificaciones y consulta
-- =============================================================================
-- Estas tablas faltaban en 001, y su ausencia era un error de razonamiento mio:
-- deje fuera las de webhooks diciendo que "AWS IoT Core ya notifica al comercio".
-- No es lo mismo.
--
--   AWS IoT Core   →  notifica al DISPOSITIVO el resultado de SU pago, ahora.
--   Webhooks       →  notifican al SISTEMA del comercio eventos que ocurren
--                     cuando el terminal esta apagado.
--
-- Un contracargo llega 30 dias despues. Una liquidacion se acredita de
-- madrugada. Un reembolso lo inicia el backoffice. El POS no esta encendido para
-- ninguno de los tres, y aun asi el comercio tiene que enterarse.
--
-- Y hay una tercera necesidad distinta de las dos: que el comercio CONSULTE sus
-- operaciones. Eso no son webhooks ni eventos, es una API de lectura sobre las
-- tablas que ya existen. Lo que falta para eso no es un modelo de datos nuevo:
-- es como autenticar al comercio. De ahi merchant_api_keys.
--
-- Del modelo de referencia de Stripe se recuperan las 3 tablas de webhooks que
-- habia descartado. merchant_api_keys es un anadido nuestro: Stripe la tiene
-- fuera de su modelo publico de datos.
--
-- Idempotente.
-- =============================================================================

BEGIN;
SET search_path TO acquiring, public;

DO $$ BEGIN
    CREATE TYPE webhook_status AS ENUM ('ENABLED', 'DISABLED', 'FAILING');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE delivery_status AS ENUM ('PENDING', 'DELIVERED', 'FAILED', 'ABANDONED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ── merchant_api_keys ────────────────────────────────────────────────────────
-- Lo que permite que el comercio consulte SUS operaciones y solo las suyas.
--
-- Se guarda el HASH, nunca la clave. Si esta tabla se filtra, no se puede
-- suplantar a nadie. Es la misma razon por la que no se guardan contrasenas en
-- claro, y en un adquirente pesa mas: esa clave mueve dinero.
CREATE TABLE IF NOT EXISTS merchant_api_keys (
    id              BIGSERIAL    PRIMARY KEY,
    merchant_id     BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    -- Prefijo visible, para que el comercio identifique la clave en su panel sin
    -- que tengamos que mostrarla entera. Convencion de la industria.
    key_prefix      VARCHAR(12)  NOT NULL UNIQUE,      -- 'sk_demo_a1b2'
    key_hash        CHAR(64)     NOT NULL,             -- SHA-256 de la clave completa
    label           VARCHAR(64),
    -- Solo lectura por defecto: una clave de consulta no debe poder cobrar.
    scopes          TEXT[]       NOT NULL DEFAULT ARRAY['read:charges','read:payouts'],
    last_used_at    TIMESTAMPTZ,
    expires_at      TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_api_keys_merchant ON merchant_api_keys (merchant_id)
    WHERE revoked_at IS NULL;

COMMENT ON TABLE merchant_api_keys IS
    'Credenciales del comercio para consultar SUS operaciones. Se guarda el hash, nunca la clave';
COMMENT ON COLUMN merchant_api_keys.scopes IS
    'Permisos. Por defecto solo lectura: una clave de consulta no debe poder cobrar';


-- ── webhook_endpoints ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_endpoints (
    id                   BIGSERIAL      PRIMARY KEY,
    merchant_id          BIGINT         NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    url                  VARCHAR(500)   NOT NULL,
    -- Con esto el comercio firma y verifica que el webhook viene de nosotros y
    -- que nadie lo manipulo. Sin firma, cualquiera puede inventarse un
    -- "contracargo resuelto a tu favor".
    signing_secret       VARCHAR(64)    NOT NULL,
    enabled_events       JSONB          NOT NULL DEFAULT '[]',
    status               webhook_status NOT NULL DEFAULT 'ENABLED',
    api_version          VARCHAR(16)    NOT NULL DEFAULT '1.0',
    -- Tras N fallos seguidos el endpoint se deshabilita solo, para no reintentar
    -- eternamente contra un servidor caido.
    consecutive_failures INTEGER        NOT NULL DEFAULT 0 CHECK (consecutive_failures >= 0),
    created_at           TIMESTAMPTZ    NOT NULL DEFAULT NOW(),
    disabled_at          TIMESTAMPTZ,

    CONSTRAINT webhook_url_is_https CHECK (url LIKE 'https://%')
);

CREATE INDEX IF NOT EXISTS idx_webhook_endpoints_merchant
    ON webhook_endpoints (merchant_id) WHERE status = 'ENABLED';

COMMENT ON CONSTRAINT webhook_url_is_https ON webhook_endpoints IS
    'Solo HTTPS: por aqui viajan importes y referencias de operaciones';


-- ── webhook_events ───────────────────────────────────────────────────────────
-- El evento ocurrio. Se persiste ANTES de intentar entregarlo, y por eso esta
-- separado de las entregas: el hecho de negocio es uno, los intentos de
-- entrega son muchos.
CREATE TABLE IF NOT EXISTS webhook_events (
    id               BIGSERIAL     PRIMARY KEY,
    merchant_id      BIGINT        NOT NULL REFERENCES merchants(id) ON DELETE CASCADE,
    event_reference  VARCHAR(40)   NOT NULL UNIQUE,     -- 'evt_01JQ8Z3K5M'
    -- 'charge.captured', 'charge.failed', 'dispute.opened', 'payout.paid',
    -- 'refund.created'
    event_type       VARCHAR(48)   NOT NULL,
    api_version      VARCHAR(16)   NOT NULL DEFAULT '1.0',
    object_type      VARCHAR(32)   NOT NULL,            -- 'charge', 'dispute'
    object_id        VARCHAR(40)   NOT NULL,            -- charge_reference, etc.
    payload          JSONB         NOT NULL,
    occurred_at      TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    created_at       TIMESTAMPTZ   NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_webhook_events_merchant
    ON webhook_events (merchant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_webhook_events_object
    ON webhook_events (object_type, object_id);

COMMENT ON TABLE webhook_events IS
    'El hecho de negocio. Se persiste antes de intentar entregarlo, y sobrevive a los fallos de entrega';


-- ── webhook_deliveries ───────────────────────────────────────────────────────
-- Cada intento. Una fila por intento y no un contador, porque cuando el comercio
-- dice "no me llego" hay que poder ensenarle que se intento 5 veces, con que
-- codigo respondio su servidor y cuando.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    id               BIGSERIAL       PRIMARY KEY,
    event_id         BIGINT          NOT NULL REFERENCES webhook_events(id) ON DELETE CASCADE,
    endpoint_id      BIGINT          NOT NULL REFERENCES webhook_endpoints(id) ON DELETE CASCADE,
    attempt_number   SMALLINT        NOT NULL DEFAULT 1 CHECK (attempt_number > 0),
    status           delivery_status NOT NULL DEFAULT 'PENDING',
    response_status  SMALLINT,
    response_body    TEXT,
    duration_ms      INTEGER         CHECK (duration_ms >= 0),
    error_message    VARCHAR(500),
    -- Reintento con retroceso exponencial. NULL = no habra mas intentos.
    next_attempt_at  TIMESTAMPTZ,
    attempted_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    UNIQUE (event_id, endpoint_id, attempt_number)
);

-- Los que toca reintentar: es la consulta del worker de entregas.
CREATE INDEX IF NOT EXISTS idx_deliveries_retry
    ON webhook_deliveries (next_attempt_at)
    WHERE status = 'PENDING' AND next_attempt_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_deliveries_event ON webhook_deliveries (event_id);

COMMENT ON TABLE webhook_deliveries IS
    'Un intento por fila, no un contador: cuando el comercio reclama hay que poder ensenar el historial';


-- ── Vista para el comercio ───────────────────────────────────────────────────
-- Lo que el comercio ve cuando consulta sus operaciones. NO expone el schema
-- interno: ni claves primarias, ni nuestros identificadores internos, ni datos
-- de otros comercios.
--
-- El filtrado por comercio NO va aqui: lo aplica la API con el merchant_id de la
-- clave autenticada. Una vista sin filtro que se consulta con el filtro puesto
-- es mas seguro que una vista que "sabe" quien pregunta.
CREATE OR REPLACE VIEW v_merchant_operations AS
SELECT
    m.merchant_id,
    c.charge_reference        AS operation_id,
    pi.intent_reference,
    t.terminal_id,
    c.created_at::date        AS operation_date,
    c.channel::text           AS payment_method,
    c.status::text            AS status,
    (c.amount_minor / 100.0)::NUMERIC(14,2)  AS amount,
    c.currency_code           AS currency,
    -- Lo que el comercio mas pregunta: cuanto le queda de verdad.
    (bt.fee_minor / 100.0)::NUMERIC(14,2)    AS fee,
    (bt.net_minor / 100.0)::NUMERIC(14,2)    AS net_amount,
    bt.available_on           AS funds_available_on,
    -- La referencia que necesita para reclamar
    c.rrn,
    c.auth_code,
    c.response_code,
    pm.brand                  AS card_brand,
    pm.last_four              AS card_last_four,
    -- Si hay contracargo abierto, el comercio tiene que verlo aqui
    d.dispute_reference,
    d.status                  AS dispute_status,
    d.evidence_due_by,
    c.created_at,
    c.captured_at
FROM charges c
JOIN merchants m         ON m.id  = c.merchant_id
JOIN payment_intents pi  ON pi.id = c.payment_intent_id
LEFT JOIN terminals t    ON t.id  = c.terminal_id
LEFT JOIN payment_methods pm ON pm.id = c.payment_method_id
LEFT JOIN balance_transactions bt
       ON bt.charge_id = c.id AND bt.source_type = 'CHARGE'
LEFT JOIN disputes d     ON d.charge_id = c.id;

COMMENT ON VIEW v_merchant_operations IS
    'Consulta de operaciones del comercio. El filtro por merchant_id lo aplica la API, no la vista';

COMMIT;
