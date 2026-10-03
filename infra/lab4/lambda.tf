# =============================================================================
# La Lambda del scoring
# =============================================================================

resource "aws_cloudwatch_log_group" "scorer" {
  name              = "/aws/lambda/${local.name_prefix}-scorer"
  retention_in_days = 7

  tags = { Name = "${local.name_prefix}-scorer-logs" }
}

# Un solo fichero sin dependencias externas: boto3 ya viene en el runtime de
# Lambda, asi que no hace falta ni capa ni contenedor.
data "archive_file" "scorer" {
  type        = "zip"
  source_file = "${path.module}/../../labs/04-streaming-online-insight/risk_scorer.py"
  output_path = "${path.module}/.terraform/risk_scorer.zip"
}

resource "aws_lambda_function" "scorer" {
  function_name = "${local.name_prefix}-scorer"
  description   = "Scoring de riesgo online sobre los eventos de pago"
  role          = aws_iam_role.scorer.arn

  filename         = data.archive_file.scorer.output_path
  source_code_hash = data.archive_file.scorer.output_base64sha256
  handler          = "risk_scorer.lambda_handler"
  runtime          = "python3.12"

  # El scoring son dos llamadas a DynamoDB: si tarda mas de 10 s es que algo
  # esta mal, y un timeout largo solo alarga el fallo.
  timeout = 10

  # 256 MB no es por memoria sino por CPU: Lambda asigna CPU en proporcion, y
  # con 128 el arranque en frio se nota en una demo en vivo.
  memory_size = 256

  # El freno de mano. Un POS con un bucle o una regla de IoT mal filtrada puede
  # invocar esto miles de veces por minuto, y cada invocacion escribe en
  # DynamoDB. Con 10 concurrentes, un accidente cuesta centimos en vez de una
  # sorpresa en la factura.
  reserved_concurrent_executions = 10

  # X-Ray en un lab sobre latencia no es burocracia: es lo que permite ensenar
  # cuanto tarda de verdad el camino "reaccionar" frente al de "recordar".
  tracing_config {
    mode = "Active"
  }

  environment {
    variables = {
      VELOCITY_TABLE        = aws_dynamodb_table.velocity.name
      WINDOW_SECONDS        = tostring(var.window_seconds)
      MAX_TX_PER_WINDOW     = tostring(var.max_tx_per_window)
      MAX_AMOUNT_PER_WINDOW = tostring(var.max_amount_per_window)
      MAX_DECLINE_RATIO     = tostring(var.max_decline_ratio)
      HIGH_RISK_AMOUNT      = tostring(var.high_risk_amount)
      # Las tiendas online agregan a todos sus compradores: otros umbrales.
      MAX_TX_PER_WINDOW_ONLINE     = tostring(var.max_tx_per_window_online)
      MAX_AMOUNT_PER_WINDOW_ONLINE = tostring(var.max_amount_per_window_online)
    }
  }

  depends_on = [
    aws_iam_role_policy.scorer,
    aws_cloudwatch_log_group.scorer,
  ]

  tags = { Name = "${local.name_prefix}-scorer" }
}

# Sin esto, la regla de IoT no puede invocarla y el fallo aparece en los logs de
# IoT y no en los de Lambda, que es donde se busca primero.
resource "aws_lambda_permission" "iot" {
  statement_id  = "AllowIoTRuleInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.scorer.function_name
  principal     = "iot.amazonaws.com"
  source_arn    = aws_iot_topic_rule.events.arn
}
