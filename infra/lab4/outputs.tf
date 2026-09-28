output "velocity_table_name" {
  description = "Tabla de DynamoDB con los contadores y las alertas"
  value       = aws_dynamodb_table.velocity.name
}

output "scorer_function_name" {
  description = "Lambda que evalua las reglas de riesgo"
  value       = aws_lambda_function.scorer.function_name
}

output "scorer_log_group" {
  description = "Donde ver el scoring en vivo durante la demo"
  value       = aws_cloudwatch_log_group.scorer.name
}

output "iot_topic_filter" {
  description = "Topic MQTT en el que publica el POS"
  value       = local.iot_topic_filter
}

output "firehose_stream_name" {
  description = "Stream que aterriza los eventos en el lago"
  value       = aws_kinesis_firehose_delivery_stream.events.name
}

output "events_s3_path" {
  description = "Zona del lago donde caen los eventos del stream"
  value       = "s3://${local.s3_bucket}/raw/events/"
}

output "thresholds" {
  description = "Umbrales activos del scoring"
  value = {
    window_seconds        = var.window_seconds
    max_tx_per_window     = var.max_tx_per_window
    max_amount_per_window = var.max_amount_per_window
    max_decline_ratio     = var.max_decline_ratio
    high_risk_amount      = var.high_risk_amount
  }
}
