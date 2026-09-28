-- =============================================================================
-- FinCorp Payments — schema transaccional del ADQUIRENTE
-- =============================================================================
-- Aurora PostgreSQL Serverless v2.
--
-- POSICION EN LA CADENA DE PAGOS. Esto es importante y condiciona todo el
-- modelo: NO somos el comercio. Somos el adquirente.
--
--   Smart Coffee  ─────▶  FinCorp Payments  ─────▶  Red (Visa)  ─────▶  Emisor
--   (comercio,            (NOSOTROS:                (marca)             (banco
--    afiliado)             adquirente)                                   del
--                                                                     tarjetahabiente)
--
-- El comercio vende cafe y le importan productos, inventario y turnos. A
-- nosotros no. A un adquirente le importa:
--
--   - Afiliar comercios y hacerles KYC  ........  merchants
--   - Provisionar terminales  ...................  terminals
--   - Autorizar y capturar pagos  ...............  payment_intents, charges
--   - Llevar el libro mayor: bruto, comision, neto  balance_transactions
--   - Liquidar al comercio  .....................  payouts
--   - Gestionar devoluciones y contracargos  ....  refunds, disputes
--
-- MODELO DE REFERENCIA
-- Adaptado del modelo "Stripe Payments" de drawDB
-- (https://www.drawdb.app/examples/stripe-payments), 13 tablas. Se usan 9 y se
-- anaden dos cosas que Stripe no modela porque nace online-first:
--
--   1. terminals    Un adquirente fisico provisiona dispositivos con TID.
--   2. ISO 8583     El mundo card-present habla RRN, STAN y response codes.
--                   Son los data elements DE 37, DE 11 y DE 39 del estandar.
--
-- Fuera quedan webhook_endpoints, webhook_events y webhook_deliveries: son como
-- el comercio se entera de los eventos, y en la demo eso lo hace AWS IoT Core.
--
-- CONVENCIONES
--   *_minor   Dinero como ENTERO en unidades menores (centimos). S/ 18.50 = 1850.
--             Nunca FLOAT: 0.1 + 0.2 no da 0.3 en binario, y en dinero eso es un
--             descuadre. Es la convencion del modelo de Stripe.
--   *_bps     Tasas en puntos basicos. 18% de IGV = 1800 bps. MDR de 2.99% = 299.
--
-- DEMO: no se procesan pagos reales. No hay PAN, no hay CVV real, no hay
-- conexion con ninguna red de tarjetas. Ver payments/README.md.
--
-- Idempotente: se puede reejecutar.
-- =============================================================================

BEGIN;

CREATE SCHEMA IF NOT EXISTS acquiring;
SET search_path TO acquiring, public;

-- ── Tipos ────────────────────────────────────────────────────────────────────

DO $$ BEGIN
    CREATE TYPE merchant_status AS ENUM ('PENDING_KYC', 'ACTIVE', 'SUSPENDED', 'TERMINATED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- El nivel de riesgo del comercio. Es lo que el corpus de compliance del Lab 3
-- llama "risk_level" y lo que determina la diligencia debida aplicable.
DO $$ BEGIN
    CREATE TYPE risk_level AS ENUM ('LOW', 'MEDIUM', 'HIGH');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE terminal_status AS ENUM ('PROVISIONED', 'ACTIVE', 'INACTIVE', 'DECOMMISSIONED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- Ciclo de vida de la intencion de pago, tomado del modelo de Stripe.
DO $$ BEGIN
    CREATE TYPE intent_status AS ENUM (
        'REQUIRES_PAYMENT_METHOD',  -- creada, sin medio de pago asignado
        'REQUIRES_CONFIRMATION',    -- el QR esta en pantalla, esperando al pagador
        'PROCESSING',               -- enviada a la red
        'SUCCEEDED',
        'CANCELED',
        'FAILED'
    );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE charge_status AS ENUM ('AUTHORIZED', 'CAPTURED', 'FAILED', 'REVERSED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE tender_channel AS ENUM ('CARD_PRESENT', 'QR');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- El libro mayor distingue el origen de cada movimiento.
DO $$ BEGIN
    CREATE TYPE ledger_source AS ENUM ('CHARGE', 'REFUND', 'DISPUTE', 'PAYOUT', 'FEE_ADJUSTMENT');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
    CREATE TYPE payout_status AS ENUM ('PENDING', 'IN_TRANSIT', 'PAID', 'FAILED');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ── merchants ────────────────────────────────────────────────────────────────
-- Los comercios afiliados. Smart Coffee es uno de ellos.
CREATE TABLE IF NOT EXISTS merchants (
    id                  BIGSERIAL       PRIMARY KEY,
    -- MID: el identificador del comercio en la red de tarjetas.
    -- ISO 8583 DE 42 (Card Acceptor ID), 15 caracteres.
    merchant_id         VARCHAR(15)     NOT NULL UNIQUE,
    legal_name          VARCHAR(160)    NOT NULL,
    trade_name          VARCHAR(120)    NOT NULL,   -- 'Smart Coffee'
    tax_id              VARCHAR(20),                -- RUC en Peru

    -- MCC: Merchant Category Code, ISO 18245 / ISO 8583 DE 18.
    -- 5812 = Eating Places and Restaurants. 5814 seria fast food.
    -- El MCC determina interchange y es una senal de riesgo por si mismo.
    mcc                 CHAR(4)         NOT NULL,
    country_code        CHAR(2)         NOT NULL DEFAULT 'PE',
    default_currency    CHAR(3)         NOT NULL DEFAULT 'PEN',

    status              merchant_status NOT NULL DEFAULT 'PENDING_KYC',
    risk_level          risk_level      NOT NULL DEFAULT 'MEDIUM',
    kyc_completed_at    TIMESTAMPTZ,

    -- MDR (Merchant Discount Rate): lo que cobramos por procesar. Es nuestro
    -- ingreso, y la razon de que balance_transactions exista.
    mdr_bps             INTEGER         NOT NULL DEFAULT 299 CHECK (mdr_bps BETWEEN 0 AND 10000),
    fixed_fee_minor     INTEGER         NOT NULL DEFAULT 30  CHECK (fixed_fee_minor >= 0),

    charges_enabled     BOOLEAN         NOT NULL DEFAULT FALSE,
    payouts_enabled     BOOLEAN         NOT NULL DEFAULT FALSE,
    payout_schedule     VARCHAR(16)     NOT NULL DEFAULT 'DAILY',
    payout_delay_days   SMALLINT        NOT NULL DEFAULT 2 CHECK (payout_delay_days >= 0),

    settlement_account  VARCHAR(34),                -- CCI / IBAN del comercio
    metadata            JSONB           NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- No se puede cobrar por un comercio sin KYC. La base de datos lo impide;
    -- no se confia en que la aplicacion se acuerde.
    CONSTRAINT merchants_charges_need_kyc
        CHECK (NOT charges_enabled OR kyc_completed_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_merchants_risk   ON merchants (risk_level) WHERE risk_level = 'HIGH';
CREATE INDEX IF NOT EXISTS idx_merchants_status ON merchants (status);

COMMENT ON TABLE  merchants IS 'Comercios afiliados. Nosotros somos el adquirente, no el comercio';
COMMENT ON COLUMN merchants.mcc IS 'Merchant Category Code (ISO 18245 / ISO 8583 DE 18). 5812 = restaurantes';
COMMENT ON COLUMN merchants.mdr_bps IS 'Merchant Discount Rate en puntos basicos. 299 = 2.99%. Es nuestro ingreso';


-- ── terminals ────────────────────────────────────────────────────────────────
-- Lo que Stripe no modela y un adquirente fisico si: el dispositivo.
CREATE TABLE IF NOT EXISTS terminals (
    id              BIGSERIAL       PRIMARY KEY,
    merchant_id     BIGINT          NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    -- TID: ISO 8583 DE 41 (Card Acceptor Terminal ID), 8 caracteres.
    terminal_id     VARCHAR(8)      NOT NULL UNIQUE,
    label           VARCHAR(64),                    -- 'POS-01 Barra'

    -- Serial del elemento seguro ATECC608 del M5Stack. Ata cada transaccion al
    -- hardware que la origino, y es el clientId en AWS IoT Core: el mismo valor
    -- identifica al dispositivo en la base de datos y en el broker MQTT.
    device_serial   VARCHAR(64)     UNIQUE,
    device_model    VARCHAR(48),                    -- 'M5Stack Core2 for AWS IoT Kit'
    firmware_version VARCHAR(24),

    status          terminal_status NOT NULL DEFAULT 'PROVISIONED',
    last_seen_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_terminals_merchant ON terminals (merchant_id);

COMMENT ON COLUMN terminals.terminal_id IS 'TID, ISO 8583 DE 41';
COMMENT ON COLUMN terminals.device_serial IS
    'Serial del ATECC608. Mismo valor que el clientId en AWS IoT Core: une el dato al hardware';


-- ── payment_methods ──────────────────────────────────────────────────────────
-- DEMO: solo tokens ficticios. Aqui NUNCA entra un PAN.
CREATE TABLE IF NOT EXISTS payment_methods (
    id              BIGSERIAL    PRIMARY KEY,
    merchant_id     BIGINT       REFERENCES merchants(id) ON DELETE SET NULL,
    method_type     VARCHAR(16)  NOT NULL,          -- 'CARD' | 'QR'

    brand           VARCHAR(16),                    -- 'VISA'
    last_four       CHAR(4),                        -- '4242' (tarjeta de demo)
    exp_month       SMALLINT     CHECK (exp_month BETWEEN 1 AND 12),
    exp_year        SMALLINT     CHECK (exp_year BETWEEN 2024 AND 2100),

    -- El token es la unica referencia al instrumento de pago. En un adquirente
    -- real lo emite el proveedor de tokenizacion y el PAN nunca toca esta base.
    processor_token VARCHAR(64)  NOT NULL UNIQUE,   -- 'demo_card_4242'
    -- Huella estable del instrumento: permite detectar que la misma tarjeta se
    -- usa en varios comercios sin almacenar el PAN. Es una senal antifraude.
    fingerprint     VARCHAR(64),

    status          VARCHAR(16)  NOT NULL DEFAULT 'ACTIVE',
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT NOW(),

    -- Barrera explicita: 4 digitos como maximo. Si alguien intentara meter un
    -- PAN completo aqui, la base lo rechaza.
    CONSTRAINT payment_methods_no_pan CHECK (last_four IS NULL OR length(last_four) = 4)
);

COMMENT ON TABLE payment_methods IS
    'Instrumentos tokenizados. DEMO: solo demo_card_4242. Nunca un PAN completo';


-- ── payment_intents ──────────────────────────────────────────────────────────
-- La intencion de cobro. El POS la crea al cerrar el carrito, antes de saber
-- como se va a pagar. Es lo que permite que el QR exista antes del pago.
CREATE TABLE IF NOT EXISTS payment_intents (
    id                    BIGSERIAL     PRIMARY KEY,
    -- Referencia publica. Viaja en el QR y en el evento MQTT. La genera el POS,
    -- asi que no puede depender de una secuencia de la base de datos.
    intent_reference      VARCHAR(40)   NOT NULL UNIQUE,   -- 'pi_TX10023'
    merchant_id           BIGINT        NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    terminal_id           BIGINT        REFERENCES terminals(id) ON DELETE SET NULL,
    payment_method_id     BIGINT        REFERENCES payment_methods(id) ON DELETE SET NULL,

    amount_minor          BIGINT        NOT NULL CHECK (amount_minor > 0),
    amount_received_minor BIGINT        NOT NULL DEFAULT 0 CHECK (amount_received_minor >= 0),
    currency_code         CHAR(3)       NOT NULL DEFAULT 'PEN',

    status                intent_status NOT NULL DEFAULT 'REQUIRES_PAYMENT_METHOD',
    channel               tender_channel,
    capture_method        VARCHAR(16)   NOT NULL DEFAULT 'AUTOMATIC',

    -- Lo que compro el cliente. JSONB y no una tabla de lineas: al adquirente no
    -- le importa el detalle del carrito, solo el importe. Se guarda porque la
    -- analitica del Lab 2 lo aprovecha, no porque el pago lo necesite.
    line_items            JSONB         NOT NULL DEFAULT '[]',

    statement_description VARCHAR(48),              -- lo que ve el tarjetahabiente
    last_error_code       VARCHAR(8),               -- response code ISO 8583
    cancellation_reason   VARCHAR(48),
    metadata              JSONB         NOT NULL DEFAULT '{}',
    created_at            TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    canceled_at           TIMESTAMPTZ,

    CONSTRAINT intents_received_within_amount
        CHECK (amount_received_minor <= amount_minor)
);

CREATE INDEX IF NOT EXISTS idx_intents_merchant ON payment_intents (merchant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_intents_open     ON payment_intents (status)
    WHERE status IN ('REQUIRES_PAYMENT_METHOD', 'REQUIRES_CONFIRMATION', 'PROCESSING');

COMMENT ON COLUMN payment_intents.line_items IS
    'Detalle del carrito. Al pago no le hace falta; lo guarda para la analitica del Lab 2';


-- ── charges ──────────────────────────────────────────────────────────────────
-- El movimiento de dinero, con los campos que el mundo card-present usa de
-- verdad. Aqui viven los data elements de ISO 8583.
CREATE TABLE IF NOT EXISTS charges (
    id                  BIGSERIAL     PRIMARY KEY,
    charge_reference    VARCHAR(40)   NOT NULL UNIQUE,     -- 'ch_TX10023_1'
    payment_intent_id   BIGINT        NOT NULL REFERENCES payment_intents(id) ON DELETE RESTRICT,
    merchant_id         BIGINT        NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    terminal_id         BIGINT        REFERENCES terminals(id) ON DELETE SET NULL,
    payment_method_id   BIGINT        REFERENCES payment_methods(id) ON DELETE SET NULL,

    amount_minor           BIGINT     NOT NULL CHECK (amount_minor > 0),
    amount_captured_minor  BIGINT     NOT NULL DEFAULT 0 CHECK (amount_captured_minor >= 0),
    amount_refunded_minor  BIGINT     NOT NULL DEFAULT 0 CHECK (amount_refunded_minor >= 0),
    currency_code          CHAR(3)    NOT NULL DEFAULT 'PEN',

    status              charge_status NOT NULL DEFAULT 'AUTHORIZED',
    channel             tender_channel NOT NULL,

    -- ── ISO 8583 ─────────────────────────────────────────────────────────────
    -- MTI del mensaje: 0100 peticion de autorizacion, 0110 respuesta.
    mti                 CHAR(4),
    -- DE 11 System Trace Audit Number: unico por terminal y dia.
    stan                CHAR(6),
    -- DE 37 Retrieval Reference Number: la referencia que se usa para localizar
    -- la operacion en toda la cadena. Es el dato que pide el comercio cuando
    -- reclama, y el que aparece en un contracargo.
    rrn                 CHAR(12)      UNIQUE,
    -- DE 39 Response Code: 00 aprobada, 05 no honrar, 51 fondos insuficientes,
    -- 54 tarjeta vencida, 91 emisor no disponible.
    response_code       CHAR(2),
    -- DE 38 Authorization Identification Response.
    auth_code           VARCHAR(12),
    network_transaction_id VARCHAR(48),

    failure_code        VARCHAR(16),
    failure_message     VARCHAR(160),
    risk_score          SMALLINT      CHECK (risk_score BETWEEN 0 AND 100),

    authorization_expires_at TIMESTAMPTZ,
    created_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    captured_at         TIMESTAMPTZ,

    -- Una captura no puede exceder la autorizacion, y un reembolso no puede
    -- exceder lo capturado. Reglas de negocio que la base garantiza.
    CONSTRAINT charges_capture_within_auth
        CHECK (amount_captured_minor <= amount_minor),
    CONSTRAINT charges_refund_within_capture
        CHECK (amount_refunded_minor <= amount_captured_minor),
    CONSTRAINT charges_captured_has_timestamp
        CHECK (status <> 'CAPTURED' OR captured_at IS NOT NULL),
    -- Aprobada significa response_code 00 y codigo de autorizacion.
    CONSTRAINT charges_approved_has_auth
        CHECK (status NOT IN ('AUTHORIZED', 'CAPTURED')
               OR (response_code = '00' AND auth_code IS NOT NULL))
);

CREATE INDEX IF NOT EXISTS idx_charges_merchant_date ON charges (merchant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_charges_intent        ON charges (payment_intent_id);
CREATE INDEX IF NOT EXISTS idx_charges_terminal      ON charges (terminal_id, created_at DESC);
-- Autorizaciones sin capturar: lo que un adquirente vigila a diario.
CREATE INDEX IF NOT EXISTS idx_charges_uncaptured    ON charges (status)
    WHERE status = 'AUTHORIZED';

COMMENT ON COLUMN charges.rrn IS
    'Retrieval Reference Number, ISO 8583 DE 37. La referencia para reclamos y contracargos';
COMMENT ON COLUMN charges.response_code IS
    'ISO 8583 DE 39. 00 aprobada, 05 no honrar, 51 fondos insuficientes, 54 vencida, 91 emisor caido';


-- ── refunds ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS refunds (
    id                BIGSERIAL    PRIMARY KEY,
    refund_reference  VARCHAR(40)  NOT NULL UNIQUE,
    charge_id         BIGINT       NOT NULL REFERENCES charges(id) ON DELETE RESTRICT,
    merchant_id       BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    amount_minor      BIGINT       NOT NULL CHECK (amount_minor > 0),
    currency_code     CHAR(3)      NOT NULL DEFAULT 'PEN',
    reason            VARCHAR(48),
    status            VARCHAR(16)  NOT NULL DEFAULT 'PENDING',
    failure_reason    VARCHAR(160),
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_refunds_charge ON refunds (charge_id);


-- ── disputes ─────────────────────────────────────────────────────────────────
-- Contracargos. Para un adquirente son una perdida potencial y una senal de
-- riesgo del comercio: un ratio alto de disputas puede llevar a suspenderlo.
CREATE TABLE IF NOT EXISTS disputes (
    id                    BIGSERIAL    PRIMARY KEY,
    dispute_reference     VARCHAR(40)  NOT NULL UNIQUE,
    charge_id             BIGINT       NOT NULL REFERENCES charges(id) ON DELETE RESTRICT,
    merchant_id           BIGINT       NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    amount_minor          BIGINT       NOT NULL CHECK (amount_minor > 0),
    currency_code         CHAR(3)      NOT NULL DEFAULT 'PEN',
    -- Motivo segun la red: 'FRAUD', 'PRODUCT_NOT_RECEIVED', 'DUPLICATE'...
    reason                VARCHAR(48)  NOT NULL,
    status                VARCHAR(24)  NOT NULL DEFAULT 'NEEDS_RESPONSE',
    evidence_due_by       TIMESTAMPTZ,
    evidence_submitted_at TIMESTAMPTZ,
    evidence              JSONB        NOT NULL DEFAULT '{}',
    opened_at             TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    resolved_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_disputes_merchant ON disputes (merchant_id, opened_at DESC);


-- ── balance_transactions ─────────────────────────────────────────────────────
-- EL LIBRO MAYOR. Es la tabla que convierte esto en un adquirente: cada
-- movimiento se descompone en bruto, comision y neto. La suma del neto por
-- comercio es lo que hay que liquidarle.
CREATE TABLE IF NOT EXISTS balance_transactions (
    id              BIGSERIAL      PRIMARY KEY,
    merchant_id     BIGINT         NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    source_type     ledger_source  NOT NULL,

    -- Solo una de estas referencias se rellena, segun source_type.
    charge_id       BIGINT         REFERENCES charges(id)   ON DELETE SET NULL,
    refund_id       BIGINT         REFERENCES refunds(id)   ON DELETE SET NULL,
    dispute_id      BIGINT         REFERENCES disputes(id)  ON DELETE SET NULL,
    payout_id       BIGINT,        -- FK anadida despues: payouts se define abajo

    -- El importe bruto de la operacion, con signo: positivo entra al comercio,
    -- negativo sale (reembolsos, contracargos, liquidaciones).
    gross_minor     BIGINT         NOT NULL,
    -- Nuestra comision. Siempre >= 0: se resta del bruto.
    fee_minor       BIGINT         NOT NULL DEFAULT 0 CHECK (fee_minor >= 0),
    net_minor       BIGINT         NOT NULL,
    currency_code   CHAR(3)        NOT NULL DEFAULT 'PEN',

    status          VARCHAR(16)    NOT NULL DEFAULT 'PENDING',
    -- Cuando el dinero queda disponible para liquidar: created_at +
    -- payout_delay_days del comercio.
    available_on    DATE           NOT NULL,
    created_at      TIMESTAMPTZ    NOT NULL DEFAULT NOW(),

    -- La invariante contable del adquirente. Si esto se rompe, el libro no
    -- cuadra, y es mejor que falle el INSERT que descubrirlo en la liquidacion.
    CONSTRAINT ledger_net_equals_gross_minus_fee
        CHECK (net_minor = gross_minor - fee_minor)
);

CREATE INDEX IF NOT EXISTS idx_ledger_merchant_available
    ON balance_transactions (merchant_id, available_on)
    WHERE status = 'PENDING';
CREATE INDEX IF NOT EXISTS idx_ledger_charge ON balance_transactions (charge_id);

COMMENT ON TABLE balance_transactions IS
    'Libro mayor del adquirente. net = gross - fee, garantizado por constraint';
COMMENT ON COLUMN balance_transactions.gross_minor IS
    'Con signo. Positivo entra al comercio; negativo sale (reembolso, contracargo, liquidacion)';


-- ── payouts ──────────────────────────────────────────────────────────────────
-- La liquidacion al comercio. Smart Coffee cobra lo que vendio menos el MDR.
CREATE TABLE IF NOT EXISTS payouts (
    id                    BIGSERIAL     PRIMARY KEY,
    payout_reference      VARCHAR(40)   NOT NULL UNIQUE,
    merchant_id           BIGINT        NOT NULL REFERENCES merchants(id) ON DELETE RESTRICT,
    amount_minor          BIGINT        NOT NULL CHECK (amount_minor > 0),
    currency_code         CHAR(3)       NOT NULL DEFAULT 'PEN',
    status                payout_status NOT NULL DEFAULT 'PENDING',
    method                VARCHAR(16)   NOT NULL DEFAULT 'BANK_TRANSFER',
    destination_last_four CHAR(4),
    business_date         DATE          NOT NULL,   -- dia liquidado
    arrival_date          DATE,
    failure_code          VARCHAR(24),
    initiated_at          TIMESTAMPTZ   NOT NULL DEFAULT NOW(),
    paid_at               TIMESTAMPTZ,

    CONSTRAINT payouts_paid_has_timestamp
        CHECK (status <> 'PAID' OR paid_at IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_payouts_merchant ON payouts (merchant_id, business_date DESC);

-- La FK circular se cierra aqui: un movimiento del libro puede referenciar la
-- liquidacion que lo consumio.
DO $$ BEGIN
    ALTER TABLE balance_transactions
        ADD CONSTRAINT balance_transactions_payout_fk
        FOREIGN KEY (payout_id) REFERENCES payouts(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;


-- ── idempotency_keys ─────────────────────────────────────────────────────────
-- Imprescindible en una API de pagos. El POS reintenta cuando el Wi-Fi del
-- evento falla, y un reintento NO puede cobrar dos veces.
CREATE TABLE IF NOT EXISTS idempotency_keys (
    id                   BIGSERIAL    PRIMARY KEY,
    merchant_id          BIGINT       REFERENCES merchants(id) ON DELETE CASCADE,
    idempotency_key      VARCHAR(64)  NOT NULL,
    request_method       VARCHAR(8)   NOT NULL,
    request_path         VARCHAR(160) NOT NULL,
    -- Hash del cuerpo. Si llega la misma clave con un cuerpo distinto, es un
    -- error del cliente y hay que rechazarlo, no servir la respuesta anterior.
    request_fingerprint  CHAR(64)     NOT NULL,
    status               VARCHAR(16)  NOT NULL DEFAULT 'IN_PROGRESS',
    response_status      SMALLINT,
    response_body        JSONB,
    resource_type        VARCHAR(32),
    resource_id          VARCHAR(40),
    locked_at            TIMESTAMPTZ,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    expires_at           TIMESTAMPTZ  NOT NULL DEFAULT NOW() + INTERVAL '24 hours',

    UNIQUE (merchant_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_idempotency_expiry ON idempotency_keys (expires_at);

COMMENT ON TABLE idempotency_keys IS
    'Evita el doble cobro cuando el POS reintenta. Sin esto, un Wi-Fi inestable cobra dos veces';


-- ── Vista para el Lab 1 ──────────────────────────────────────────────────────
-- Es lo que DMS replica al data lake. Una vista y no las tablas crudas: aisla el
-- schema interno del consumidor y entrega el dato ya aplanado.
--
-- Las columnas coinciden a proposito con el vocabulario que ya usan los Labs 2 y
-- 3 (amount, currency, status, risk_level), para que el pipeline existente y el
-- corpus de compliance sigan encajando.
CREATE OR REPLACE VIEW v_transactions_for_ingestion AS
SELECT
    c.charge_reference                         AS transaction_id,
    m.merchant_id                              AS customer_id,   -- el comercio es nuestro cliente
    m.trade_name                               AS merchant_name,
    m.mcc,
    m.risk_level::text                         AS risk_level,
    t.terminal_id,
    t.device_serial,
    (c.amount_minor / 100.0)::NUMERIC(14,2)    AS amount,
    c.currency_code                            AS currency,
    c.channel::text                            AS payment_method,
    c.status::text                             AS status,
    c.response_code,
    c.rrn,
    c.auth_code,
    (bt.fee_minor / 100.0)::NUMERIC(14,2)      AS fee_amount,
    (bt.net_minor / 100.0)::NUMERIC(14,2)      AS net_amount,
    c.created_at::date                         AS transaction_date,
    c.created_at,
    c.captured_at
FROM charges c
JOIN merchants m ON m.id = c.merchant_id
LEFT JOIN terminals t ON t.id = c.terminal_id
LEFT JOIN balance_transactions bt
       ON bt.charge_id = c.id AND bt.source_type = 'CHARGE';

COMMENT ON VIEW v_transactions_for_ingestion IS
    'Vista plana que DMS replica al data lake (Lab 1). Alineada con el vocabulario de los Labs 2 y 3';

COMMIT;
