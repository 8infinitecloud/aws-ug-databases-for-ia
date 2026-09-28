# =============================================================================
# Glue: Database, Job, Crawler y Data Quality Ruleset
# =============================================================================

resource "aws_cloudwatch_log_group" "glue_job" {
  name              = "/aws-glue/jobs/${var.project_name}-${var.environment}"
  retention_in_days = 7 # en un lab no hace falta retencion larga
}

# El Catalog es lo que convierte "archivos en S3" en "tablas con significado".
# Los parameters son metadata de negocio: quien es dueno del dato y de donde sale.
resource "aws_glue_catalog_database" "main" {
  name        = var.glue_database_name
  description = "Base de datos del catalogo FinCorp - tablas curadas del Lab 2"

  parameters = {
    lab             = "Lab2-Transformation"
    owner           = "data-engineering-team@fincorp.com"
    business_domain = "Finance / Transactions"
    source          = "DMS via S3 raw zone (Lab 1)"
  }
}

# Python Shell, no PySpark: arranca en ~20s en vez de ~3min y el dataset del
# lab cabe en pandas. La diferencia se nota en una demo en vivo.
resource "aws_glue_job" "etl" {
  name        = "${var.project_name}-${var.environment}-etl"
  description = "Lee S3 raw/, transforma, valida calidad y escribe curated/ en Parquet"
  role_arn    = aws_iam_role.glue.arn

  command {
    name            = "pythonshell"
    python_version  = "3"
    script_location = "s3://${local.s3_bucket}/${var.glue_job_script_s3_key}"
  }

  # 0.0625 DPU = 1/16, el minimo de Python Shell: ~$0.0006 por ejecucion de 3 min.
  max_capacity = 0.0625
  glue_version = "3.0"
  max_retries  = 0
  timeout      = 10

  default_arguments = {
    "--S3_BUCKET"       = local.s3_bucket
    "--RAW_PREFIX"      = "raw/"
    "--CURATED_PREFIX"  = "curated/"
    "--REJECTED_PREFIX" = "rejected/"
    "--REPORTS_PREFIX"  = "quality-reports/"
    "--DATABASE_NAME"   = var.glue_database_name
    "--JOB_NAME"        = "${var.project_name}-${var.environment}-etl"

    "--enable-continuous-cloudwatch-log" = "true"
    "--enable-metrics"                   = "true"
  }

  execution_property {
    max_concurrent_runs = 1
  }

  tags = { Name = "${var.project_name}-${var.environment}-etl" }
}

# Sin schedule: se lanza a mano tras el Glue Job (PASO 3 de run_job.sh), para
# que en la demo se vea aparecer la tabla en el Catalog.
resource "aws_glue_crawler" "curated" {
  name          = "${var.project_name}-${var.environment}-crawler"
  description   = "Descubre el schema de curated/transactions/ y actualiza el Catalog"
  role          = aws_iam_role.glue.arn
  database_name = aws_glue_catalog_database.main.name

  s3_target {
    path = "s3://${local.s3_bucket}/curated/transactions/"
  }

  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "LOG"
  }

  configuration = jsonencode({
    Version = 1.0
    CrawlerOutput = {
      Partitions = { AddOrUpdateBehavior = "InheritFromTable" }
      Tables     = { AddOrUpdateBehavior = "MergeNewColumns" }
    }
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
  })

  tags = { Name = "${var.project_name}-${var.environment}-crawler" }
}

# Sin target_table a proposito: la tabla `transactions` no existe hasta que corre
# el Crawler, y declararla aqui falla con EntityNotFoundException. El target
# solo se puede fijar al crear el ruleset, asi que queda standalone. No afecta
# al lab: la validacion efectiva la ejecuta glue_job.py en Python sobre estas
# mismas reglas.
resource "aws_glue_data_quality_ruleset" "main" {
  name        = "${var.project_name}-${var.environment}-dq-ruleset"
  description = "Calidad de las autorizaciones de pago. 8 reglas de columna + 2 de regla de negocio"

  # Este ruleset DUPLICA labs/02-.../quality_rules.dqdl. Los dos tienen que
  # decir lo mismo, y un test lo comprueba.
  ruleset = <<-DQDL
    Rules = [
      Completeness "amount" >= 0.95,
      IsComplete "transaction_id",
      IsComplete "customer_id",
      Uniqueness "transaction_id" = 1.0,
      ColumnValues "payment_method" in ["CARD_PRESENT", "QR"],
      ColumnValues "status" in ["CAPTURED", "FAILED", "REFUNDED", "REVERSED"],
      ColumnValues "currency" in ["PEN", "USD", "EUR"],
      ColumnLength "transaction_id" between 1 and 40,
      CustomSql "SELECT COUNT(*) FROM primary WHERE amount IS NOT NULL AND fee_amount IS NOT NULL AND net_amount IS NOT NULL AND ABS(amount - fee_amount - net_amount) > 0.01" = 0,
      CustomSql "SELECT COUNT(*) FROM primary WHERE status = 'CAPTURED' AND response_code <> '00'" = 0
    ]
  DQDL

  # Sin target_table a proposito: la tabla `transactions` no existe hasta que corre
  # el Crawler, y declararla aqui falla con EntityNotFoundException. TargetTable
  # solo se puede fijar al crear el ruleset, asi que queda standalone. No afecta
  # al lab: la validacion efectiva la ejecuta glue_job.py sobre estas mismas reglas.
  tags = { Name = "${var.project_name}-${var.environment}-dq-ruleset" }

  depends_on = [aws_glue_catalog_database.main]
}
