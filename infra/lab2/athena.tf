# =============================================================================
# Athena — la capa de consulta del lakehouse
# =============================================================================
# El Crawler deja la tabla en el Catalog, pero el Catalog por si solo no
# consulta nada: es un indice. Athena es lo que convierte ese indice en SQL
# sobre el Parquet que dejo el Lab 2, sin mover los datos ni levantar un
# cluster.
#
# Esto es lo que hace que la plataforma sea un lakehouse y no solo un lago: la
# zona curada se consulta como una tabla, pero sigue siendo S3.

resource "aws_athena_workgroup" "main" {
  name        = "${var.project_name}-${var.environment}"
  description = "Consultas sobre la zona curada del Lab 2"

  configuration {
    # Sin esto, cada usuario puede mandar sus resultados a donde quiera y la
    # configuracion de cifrado del workgroup se vuelve decorativa.
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true

    # El freno de mano. Athena cobra por byte escaneado, y una consulta sin
    # filtro de particion sobre una tabla grande puede costar mucho mas de lo
    # que nadie espera de un SELECT. 1 GB no molesta a este dataset —son unos
    # pocos MB— pero deja el patron a la vista.
    bytes_scanned_cutoff_per_query = 1073741824 # 1 GB

    result_configuration {
      output_location = "s3://${local.s3_bucket}/athena-results/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }

  # Los resultados son datos derivados: al destruir, que se vayan con el resto.
  force_destroy = true

  tags = {
    Name = "${var.project_name}-${var.environment}-athena"
  }
}

# -----------------------------------------------------------------------------
# Consultas guardadas
# -----------------------------------------------------------------------------
# Aparecen ya escritas en la consola de Athena, en "Saved queries". Quien sigue
# el taller no tiene que copiar SQL de un README a mano, que es donde se pierde
# el tiempo y aparecen las erratas.

resource "aws_athena_named_query" "volumen_por_comercio" {
  name        = "1. Volumen y comision por comercio"
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.main.name
  description = "La vista del negocio del adquirente: cuanto mueve cada comercio y cuanto deja"

  query = <<-SQL
    -- Lo que un adquirente mira primero: quien mueve volumen y que margen deja.
    -- customer_id es el comercio, porque el cliente del adquirente es el comercio.
    SELECT
        customer_id                       AS merchant_id,
        merchant_name,
        mcc,
        COUNT(*)                          AS transacciones,
        ROUND(SUM(amount), 2)             AS volumen_bruto,
        ROUND(SUM(fee_amount), 2)         AS comision,
        ROUND(SUM(net_amount), 2)         AS a_liquidar,
        -- El MDR efectivo en puntos basicos, que es como se habla en el sector.
        ROUND(10000.0 * SUM(fee_amount) / NULLIF(SUM(amount), 0), 1) AS mdr_bps
    FROM transactions
    WHERE status = 'CAPTURED'
    GROUP BY customer_id, merchant_name, mcc
    ORDER BY volumen_bruto DESC
    LIMIT 20;
  SQL
}

resource "aws_athena_named_query" "aprobacion_por_mcc" {
  name        = "2. Tasa de aprobacion por MCC"
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.main.name
  description = "El MCC como senal de riesgo: que rubros aprueban peor"

  query = <<-SQL
    -- El MCC dice a que se dedica el comercio, y es una senal de riesgo: los
    -- rubros con mas fraude aprueban peor porque los emisores desconfian.
    SELECT
        mcc,
        risk_level,
        COUNT(*)                                                       AS intentos,
        SUM(CASE WHEN status = 'CAPTURED' THEN 1 ELSE 0 END)           AS aprobadas,
        ROUND(100.0 * SUM(CASE WHEN status = 'CAPTURED' THEN 1 ELSE 0 END)
              / COUNT(*), 1)                                           AS tasa_aprobacion
    FROM transactions
    GROUP BY mcc, risk_level
    ORDER BY tasa_aprobacion ASC;
  SQL
}

resource "aws_athena_named_query" "declinaciones" {
  name        = "3. Declinaciones por codigo ISO 8583"
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.main.name
  description = "Por que dicen que no los emisores (DE 39)"

  query = <<-SQL
    -- El DE 39 de ISO 8583. El 00 aprueba; el resto son motivos distintos y se
    -- tratan distinto: un 51 (saldo) se reintenta, un 54 (caducada) no.
    SELECT
        response_code,
        CASE response_code
            WHEN '00' THEN 'Aprobada'
            WHEN '05' THEN 'No honrar'
            WHEN '51' THEN 'Fondos insuficientes'
            WHEN '54' THEN 'Tarjeta expirada'
            WHEN '91' THEN 'Emisor no disponible'
            ELSE 'Otro'
        END                              AS significado,
        COUNT(*)                         AS n,
        ROUND(100.0 * COUNT(*) / SUM(COUNT(*)) OVER (), 1) AS porcentaje
    FROM transactions
    GROUP BY response_code
    ORDER BY n DESC;
  SQL
}

resource "aws_athena_named_query" "particiones" {
  name        = "4. Por que particionamos (coste)"
  workgroup   = aws_athena_workgroup.main.id
  database    = aws_glue_catalog_database.main.name
  description = "La misma pregunta con y sin filtro de particion: compara los bytes escaneados"

  query = <<-SQL
    -- Ejecuta las dos y mira "Data scanned" en cada una.
    --
    -- La segunda lee un solo directorio de S3 porque el filtro cae sobre las
    -- columnas de particion. La primera abre todos. Athena cobra por byte
    -- escaneado, asi que el particionado no es orden: es la factura.

    -- (a) sin filtro de particion — escanea todo
    SELECT COUNT(*) AS n, ROUND(SUM(amount), 2) AS volumen
    FROM transactions;

    -- (b) con filtro de particion — escanea un directorio
    -- SELECT COUNT(*) AS n, ROUND(SUM(amount), 2) AS volumen
    -- FROM transactions
    -- WHERE year = '2024' AND month = '03';
  SQL
}
