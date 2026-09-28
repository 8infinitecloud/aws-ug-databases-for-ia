# =============================================================================
# Firehose — la derivacion hacia el lago
# =============================================================================
# Esto NO es el camino del insight online: es la toma que alimenta los labs 1-2
# con los mismos eventos. El scoring va por la Lambda, en milisegundos; esto
# aterriza el evento en S3 para que exista tambien en la historia.
#
# Los 60 s de buffer son el minimo del servicio, y son la razon de que Firehose
# no sirva para reaccionar: sirve para guardar.

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/${local.name_prefix}-events"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_stream" "firehose_s3" {
  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_kinesis_firehose_delivery_stream" "events" {
  name        = "${local.name_prefix}-events"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = local.s3_bucket_arn

    # Zona propia: los eventos no se mezclan con las transacciones que trae DMS.
    # El Lab 2 esta calibrado sobre aquellas, y meter estas en el mismo prefijo
    # romperia sus numeros.
    prefix              = "raw/events/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "raw/events-errors/!{firehose:error-output-type}/"

    buffering_size     = 1 # MB
    buffering_interval = var.firehose_buffer_seconds
    compression_format = "GZIP"

    cloudwatch_logging_options {
      enabled         = true
      log_group_name  = aws_cloudwatch_log_group.firehose.name
      log_stream_name = aws_cloudwatch_log_stream.firehose_s3.name
    }
  }

  # Cifrado del propio stream, no solo del destino. Con clave gestionada por AWS
  # no cuesta nada; una CMK propia serian 1 USD/mes por proteger datos
  # sinteticos de una demo.
  server_side_encryption {
    enabled = true
  }

  tags = { Name = "${local.name_prefix}-events" }
}
