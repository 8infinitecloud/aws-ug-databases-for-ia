# =============================================================================
# La regla de IoT Core — donde se bifurca el camino
# =============================================================================
# Un evento, dos destinos, y la diferencia entre ellos es el lab entero:
#
#   Lambda    -> scoring en milisegundos          (reaccionar)
#   Firehose  -> S3, con 60 s de buffer           (recordar)
#
# La regla los alimenta a la vez. Si la Lambda falla, el evento sigue llegando
# al lago; si Firehose falla, el scoring ya ocurrio.

resource "aws_iot_topic_rule" "events" {
  name        = replace("${local.name_prefix}_events", "-", "_")
  description = "Enruta los eventos de pago del POS al scoring y al data lake"
  enabled     = true

  # SQL de IoT, no de una base de datos: se evalua sobre el mensaje al vuelo.
  # `SELECT *` pasa el evento entero; filtrar aqui ahorraria invocaciones, pero
  # el scoring necesita tambien las declinadas —son la senal de card testing—
  # asi que no hay nada que filtrar.
  sql         = "SELECT * FROM '${local.iot_topic_filter}'"
  sql_version = "2016-03-23"

  lambda {
    function_arn = aws_lambda_function.scorer.arn
  }

  firehose {
    delivery_stream_name = aws_kinesis_firehose_delivery_stream.events.name
    role_arn             = aws_iam_role.iot_rule.arn
    # Sin esto los eventos se concatenan sin separador y el JSON del objeto en
    # S3 queda ilegible para Glue.
    separator = "\n"
  }

  # Si la regla no puede entregar, que quede rastro en vez de perderse.
  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_errors.name
      role_arn       = aws_iam_role.iot_rule.arn
    }
  }
}

resource "aws_cloudwatch_log_group" "iot_errors" {
  name              = "/aws/iot/${local.name_prefix}-rule-errors"
  retention_in_days = 7
}
