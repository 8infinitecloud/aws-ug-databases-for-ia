# =============================================================================
# Firehose — la telemetria de los terminales hacia el lago
# =============================================================================
# Antes llevaba los eventos de pago, y era un duplicado: el pago ya llega al lago
# por el CDC de la base (Lab 1). Ahora lleva lo que ninguna base guarda: el
# latido, la bateria, la senal Wi-Fi y la latencia de cada terminal. Es lo que
# permite preguntar en Athena POR QUE fallo un cobro, y no solo que fallo.
#
# Los 60 s de buffer son el minimo del servicio, y son la razon de que Firehose
# no sirva para reaccionar: sirve para guardar. Lo inmediato (que terminal esta
# caido ahora) lo resuelve la regla escribiendo en DynamoDB.

resource "aws_cloudwatch_log_group" "firehose" {
  name              = "/aws/kinesisfirehose/${local.name_prefix}-telemetry"
  retention_in_days = 7
}

resource "aws_cloudwatch_log_stream" "firehose_s3" {
  name           = "S3Delivery"
  log_group_name = aws_cloudwatch_log_group.firehose.name
}

resource "aws_kinesis_firehose_delivery_stream" "telemetry" {
  name        = "${local.name_prefix}-telemetry"
  destination = "extended_s3"

  extended_s3_configuration {
    role_arn   = aws_iam_role.firehose.arn
    bucket_arn = local.s3_bucket_arn

    # Zona propia dentro de raw/: es otra fuente, como acquiring/ (DMS) y
    # legacy/. Particionada por dia, que es como se consulta la salud de una
    # flota: "que paso ayer con los terminales de este comercio".
    prefix              = "raw/telemetry/year=!{timestamp:yyyy}/month=!{timestamp:MM}/day=!{timestamp:dd}/"
    error_output_prefix = "raw/telemetry-errors/!{firehose:error-output-type}/"

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

  tags = { Name = "${local.name_prefix}-telemetry" }
}
