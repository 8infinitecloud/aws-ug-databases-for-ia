# =============================================================================
# DynamoDB — el estado de las ventanas
# =============================================================================
# Aqui es donde el Lab 4 se separa de verdad de los anteriores. Los labs 1-3
# guardan en S3 porque responden preguntas sobre el pasado y el pasado no tiene
# prisa. Este necesita leer y escribir un contador en milisegundos, y para eso
# S3 no sirve: no es lento, es que no es una base de datos.

resource "aws_dynamodb_table" "velocity" {
  name         = "${local.name_prefix}-velocity"
  billing_mode = "PAY_PER_REQUEST" # el trafico de una demo es a rafagas
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # Una sola tabla para dos cosas, distinguidas por el prefijo de la sk:
  #   W#<epoch>    contador de una ventana
  #   ALERT#<ts>#<tx>  una operacion marcada
  # Asi el dashboard lee todo lo de un terminal con una sola Query.

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  point_in_time_recovery {
    # Son datos derivados y efimeros: se reconstruyen reprocesando el stream, y
    # pagar backups continuos de contadores que expiran en minutos no tiene
    # sentido.
    enabled = false
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name = "${local.name_prefix}-velocity"
  }
}


# =============================================================================
# El ultimo estado de cada terminal
# =============================================================================
# Lo escribe directamente la regla de telemetria de IoT Core, sin Lambda: no hay
# nada que calcular, solo guardar el ultimo latido encima del anterior. Un item
# por dispositivo, sobrescrito cada ~30 s.
#
# Sin TTL a proposito: un terminal que deja de latir tiene que SEGUIR aqui, con
# su ultimo `received_at_ms` envejeciendo. Si expirara, el terminal caido
# desapareceria de la lista justo cuando hay que verlo.
resource "aws_dynamodb_table" "terminal_state" {
  name         = "${local.name_prefix}-terminal-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "device_id"

  attribute {
    name = "device_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false # se reconstruye con el siguiente latido
  }

  server_side_encryption {
    enabled = true
  }

  tags = {
    Name = "${local.name_prefix}-terminal-state"
  }
}
