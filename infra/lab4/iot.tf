# =============================================================================
# Las reglas de IoT Core — dos flujos, cada uno con un solo trabajo
# =============================================================================
# Por IoT Core pasan dos cosas distintas, y antes iban mezcladas:
#
#   <serial>/events      el PAGO, que publica el backend al cobrar
#                        -> Lambda: scoring en milisegundos     (reaccionar)
#
#   <serial>/telemetry   el DISPOSITIVO: latido, bateria, senal, latencia
#                        -> Firehose -> S3 raw/telemetry/       (recordar)
#                        -> DynamoDB: ultimo estado por terminal (vigilar)
#
# Los eventos de pago YA NO van al lago por Firehose: el pago llega al lago por
# el CDC de la base (Lab 1), que ademas trae lo que el evento no tiene —el cobro
# que cambia de estado, la devolucion, el libro mayor—. Mandarlo tambien por
# aqui era tener cada pago dos veces en raw/ con dos formas distintas.
#
# La telemetria no esta en ninguna base de datos. Si no entra por aqui, no
# entra: por eso Firehose se queda, con un trabajo que nadie mas hace.

resource "aws_iot_topic_rule" "events" {
  name        = replace("${local.name_prefix}_events", "-", "_")
  description = "Enruta los eventos de pago al scoring online"
  enabled     = true
  sql         = "SELECT * FROM '${local.iot_topic_filter}'"
  sql_version = "2016-03-23"

  lambda {
    function_arn = aws_lambda_function.scorer.arn
  }

  error_action {
    cloudwatch_logs {
      log_group_name = aws_cloudwatch_log_group.iot_errors.name
      role_arn       = aws_iam_role.iot_rule.arn
    }
  }
}

resource "aws_iot_topic_rule" "telemetry" {
  name        = replace("${local.name_prefix}_telemetry", "-", "_")
  description = "Aterriza la telemetria de los terminales en el lago y guarda su ultimo estado"
  enabled     = true
  # topic(1) es el serial: el dispositivo no tiene que repetirlo en el mensaje,
  # y uno que mintiera sobre su identidad no podria, porque la politica de IoT
  # solo le deja publicar bajo su propio serial. timestamp() es la hora en que
  # lo recibio IoT Core: la del dispositivo puede estar desfasada (el Core2 no
  # tiene RTC sincronizado hasta que hace SNTP).
  sql         = "SELECT *, topic(1) AS device_id, timestamp() AS received_at_ms FROM '${local.telemetry_topic_filter}'"
  sql_version = "2016-03-23"

  firehose {
    delivery_stream_name = aws_kinesis_firehose_delivery_stream.telemetry.name
    role_arn             = aws_iam_role.iot_rule.arn
    # Un registro por linea. Sin separador, Firehose concatena los JSON y Athena
    # no puede distinguir donde acaba uno y empieza el siguiente.
    separator = "\n"
  }

  # El ultimo estado de cada terminal, sobrescrito en cada latido. Es lo que
  # permite preguntar AHORA "que terminales llevan mas de 2 minutos sin dar
  # senales" —la regla con la que Stripe considera offline un lector— sin
  # esperar al buffer de Firehose ni a una consulta de Athena.
  dynamodbv2 {
    role_arn = aws_iam_role.iot_rule.arn
    put_item {
      table_name = aws_dynamodb_table.terminal_state.name
    }
  }

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
