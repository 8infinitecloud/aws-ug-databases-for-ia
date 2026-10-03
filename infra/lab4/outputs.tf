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
  description = "Topic MQTT de los eventos de pago (los publica el backend)"
  value       = local.iot_topic_filter
}

output "telemetry_topic_filter" {
  description = "Topic MQTT de la telemetria de los terminales"
  value       = local.telemetry_topic_filter
}

output "firehose_stream_name" {
  description = "Stream que aterriza la telemetria en el lago"
  value       = aws_kinesis_firehose_delivery_stream.telemetry.name
}

output "telemetry_s3_path" {
  description = "Zona del lago donde cae la telemetria"
  value       = "s3://${local.s3_bucket}/raw/telemetry/"
}

output "terminal_state_table" {
  description = "Tabla de DynamoDB con el ultimo latido de cada terminal"
  value       = aws_dynamodb_table.terminal_state.name
}

output "thresholds" {
  description = "Umbrales activos del scoring"
  value = {
    window_seconds        = var.window_seconds
    max_tx_per_window     = var.max_tx_per_window
    max_amount_per_window = var.max_amount_per_window
    max_decline_ratio     = var.max_decline_ratio
    high_risk_amount      = var.high_risk_amount
    max_tx_online         = var.max_tx_per_window_online
    max_amount_online     = var.max_amount_per_window_online
  }
}
