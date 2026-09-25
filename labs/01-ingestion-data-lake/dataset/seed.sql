-- =============================================================================
-- seed.sql — Schema FinCorp + datos de ejemplo para Lab 1
-- =============================================================================
-- Uso:
--   mysql -h <RDS_ENDPOINT> -u admin -p fincorp < seed.sql
--
-- Idempotente: DROP TABLE IF EXISTS antes de cada CREATE.
-- El bulk load completo se realiza desde los CSV con LOAD DATA LOCAL INFILE
-- (ver setup.sh). Este archivo solo carga las primeras 50 filas como demo
-- y verifica que el schema está correcto antes del DMS task.
-- =============================================================================

CREATE DATABASE IF NOT EXISTS fincorp CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE fincorp;

-- =============================================================================
-- TABLA: customers
-- =============================================================================
DROP TABLE IF EXISTS transactions;  -- transactions referencia customers → borrar primero
DROP TABLE IF EXISTS customers;

CREATE TABLE customers (
    customer_id     VARCHAR(10)  NOT NULL,
    name            VARCHAR(100) NOT NULL,
    email           VARCHAR(100) NOT NULL,
    country         VARCHAR(5)   NOT NULL,
    risk_level      VARCHAR(10)  NOT NULL,
    onboarding_date DATE         NOT NULL,
    created_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (customer_id),
    INDEX idx_risk_level (risk_level),
    INDEX idx_country (country),
    CONSTRAINT chk_risk_level CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Perfil de clientes FinCorp — tabla de referencia (sin problemas deliberados)';

-- =============================================================================
-- TABLA: transactions
-- =============================================================================
CREATE TABLE transactions (
    transaction_id   VARCHAR(20)    NOT NULL,
    customer_id      VARCHAR(10)    NULL,
    amount           DECIMAL(12, 2) NULL COMMENT 'NULL deliberado en ~5% de filas para Lab 2 DQ demo',
    currency         VARCHAR(3)     NOT NULL DEFAULT 'USD',
    transaction_type VARCHAR(30)    NOT NULL,
    status           VARCHAR(20)    NOT NULL,
    transaction_date VARCHAR(20)    NOT NULL COMMENT 'VARCHAR intencionalmente: mezcla de YYYY-MM-DD y DD/MM/YYYY',
    branch_id        VARCHAR(10)    NOT NULL,
    created_at       TIMESTAMP      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_customer_id (customer_id),
    INDEX idx_status (status),
    INDEX idx_branch (branch_id)
    -- PK no definida intencionalmente aquí porque DMS replica todos los campos;
    -- la constraint de unicidad se aplica en la capa curated del Lab 2.
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='Transacciones financieras — contiene problemas deliberados para Lab 2 Data Quality';

-- =============================================================================
-- DATOS DE EJEMPLO — primeras 50 filas representativas
-- (el bulk load completo se hace desde el CSV en setup.sh)
-- =============================================================================

INSERT INTO customers (customer_id, name, email, country, risk_level, onboarding_date) VALUES
('CUS001', 'Ana García1', 'ana.garcía1@gmail.com', 'PE', 'LOW', '2023-07-14'),
('CUS002', 'Carlos Rodríguez2', 'carlos.rodríguez2@yahoo.com', 'PE', 'MEDIUM', '2021-04-22'),
('CUS003', 'María López3', 'maría.lópez3@hotmail.com', 'US', 'HIGH', '2022-11-09'),
('CUS004', 'Roberto Martínez4', 'roberto.martínez4@empresa.com', 'CO', 'LOW', '2020-06-30'),
('CUS005', 'Luis González5', 'luis.gonzález5@gmail.com', 'PE', 'MEDIUM', '2024-01-15'),
('CUS006', 'Sofia Torres6', 'sofia.torres6@outlook.com', 'MX', 'LOW', '2023-03-08'),
('CUS007', 'Diego Pérez7', 'diego.pérez7@gmail.com', 'PE', 'HIGH', '2021-09-17'),
('CUS008', 'Laura Sánchez8', 'laura.sánchez8@yahoo.com', 'CL', 'LOW', '2022-05-20'),
('CUS009', 'Miguel Ramírez9', 'miguel.ramírez9@empresa.com', 'BR', 'MEDIUM', '2020-12-03'),
('CUS010', 'Valentina Cruz10', 'valentina.cruz10@hotmail.com', 'PE', 'LOW', '2025-02-28');

-- Transacciones válidas (sin problemas)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000001', 'CUS001', 1500.00, 'USD', 'DEPOSIT', 'COMPLETED', '2024-03-15', 'BR001'),
('TXN0000002', 'CUS002', 250.50, 'PEN', 'WITHDRAWAL', 'COMPLETED', '2024-04-02', 'BR003'),
('TXN0000003', 'CUS003', 8900.00, 'USD', 'TRANSFER', 'PENDING', '2024-05-18', 'BR007'),
('TXN0000004', 'CUS004', 45.75, 'PEN', 'DEPOSIT', 'COMPLETED', '2024-06-01', 'BR002'),
('TXN0000005', 'CUS005', 12000.00, 'USD', 'TRANSFER', 'COMPLETED', '2024-07-22', 'BR010'),
('TXN0000006', 'CUS006', 500.00, 'EUR', 'DEPOSIT', 'PROCESSING', '2024-08-05', 'BR015'),
('TXN0000007', 'CUS007', 3200.00, 'USD', 'WITHDRAWAL', 'COMPLETED', '2024-09-14', 'BR004'),
('TXN0000008', 'CUS008', 780.25, 'PEN', 'DEPOSIT', 'FAILED', '2024-10-30', 'BR018'),
('TXN0000009', 'CUS009', 15500.00, 'USD', 'TRANSFER', 'COMPLETED', '2024-11-11', 'BR006'),
('TXN0000010', 'CUS010', 92.00, 'PEN', 'WITHDRAWAL', 'PENDING', '2024-12-20', 'BR009');

-- Transacciones con problemas deliberados (documentados para Lab 2 demo)

-- Problema 1: amount = NULL (~5% del dataset total)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000011', 'CUS001', NULL, 'USD', 'DEPOSIT', 'COMPLETED', '2025-01-10', 'BR001'),
('TXN0000012', 'CUS003', NULL, 'PEN', 'WITHDRAWAL', 'PENDING', '2025-02-14', 'BR005'),
('TXN0000013', 'CUS005', NULL, 'USD', 'TRANSFER', 'COMPLETED', '2025-03-22', 'BR012');

-- Problema 2: transaction_id duplicado (~3% del dataset total)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000001', 'CUS008', 2100.00, 'USD', 'DEPOSIT', 'COMPLETED', '2025-04-05', 'BR016'),  -- duplicado
('TXN0000003', 'CUS009', 890.00, 'PEN', 'WITHDRAWAL', 'FAILED', '2025-04-18', 'BR003');    -- duplicado

-- Problema 3: transaction_date en formato DD/MM/YYYY (~10% del dataset total)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000016', 'CUS002', 4500.00, 'USD', 'TRANSFER', 'COMPLETED', '15/03/2025', 'BR008'),
('TXN0000017', 'CUS004', 120.00, 'PEN', 'DEPOSIT', 'PENDING', '22/04/2025', 'BR014'),
('TXN0000018', 'CUS006', 7800.00, 'USD', 'WITHDRAWAL', 'COMPLETED', '01/05/2025', 'BR002');

-- Problema 4: transaction_type con valores en español (~2% del dataset total)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000019', 'CUS007', 3300.00, 'USD', 'DEPÓSITO', 'COMPLETED', '2025-06-10', 'BR011'),
('TXN0000020', 'CUS010', 1200.00, 'PEN', 'RETIRO', 'COMPLETED', '2025-07-18', 'BR007'),
('TXN0000021', 'CUS001', 5600.00, 'USD', 'TRANSFERENCIA', 'PENDING', '2025-08-25', 'BR019');

-- Problema 5: status con case inconsistente (~8% del dataset total)
INSERT INTO transactions (transaction_id, customer_id, amount, currency, transaction_type, status, transaction_date, branch_id) VALUES
('TXN0000022', 'CUS003', 890.00, 'PEN', 'DEPOSIT', 'pending', '2025-09-01', 'BR004'),
('TXN0000023', 'CUS005', 2400.00, 'USD', 'WITHDRAWAL', 'Completed', '2025-09-05', 'BR013'),
('TXN0000024', 'CUS008', 15000.00, 'USD', 'TRANSFER', 'failed', '2025-09-10', 'BR001');

-- =============================================================================
-- VERIFICACIÓN: mostrar conteo y muestra de problemas
-- =============================================================================
SELECT 'customers' AS tabla, COUNT(*) AS total FROM customers
UNION ALL
SELECT 'transactions', COUNT(*) FROM transactions;

SELECT
    'NULL amount'          AS problema,
    COUNT(*)               AS filas
FROM transactions WHERE amount IS NULL
UNION ALL
SELECT 'Fecha DD/MM/YYYY', COUNT(*)
FROM transactions WHERE transaction_date LIKE '__/__/____'
UNION ALL
SELECT 'Tipo inválido', COUNT(*)
FROM transactions WHERE transaction_type IN ('DEPÓSITO','RETIRO','TRANSFERENCIA')
UNION ALL
SELECT 'Status lowercase/mixed', COUNT(*)
FROM transactions WHERE status NOT IN ('PENDING','COMPLETED','FAILED','PROCESSING');
