# =============================================================================
# API Gateway — la puerta de entrada
# =============================================================================
# El terminal y la web de pago QR entran por aqui. El POS tiene dos caminos hacia
# la plataforma y esto es el segundo:
#
#   MQTT       ──▶ AWS IoT Core   para eventos (fire-and-forget)
#   HTTPS      ──▶ API Gateway    para pedir algo y esperar respuesta
#
# La distincion no es arbitraria: autorizar un pago necesita una RESPUESTA
# (aprobada o rechazada), y MQTT no es un buen transporte peticion-respuesta.
# Publicar que un pago ocurrio no necesita respuesta, y ahi MQTT encaja mejor.

# HTTP API y no REST API: ~70% mas barata y aqui no hace falta nada de lo que
# REST API anade.
resource "aws_apigatewayv2_api" "main" {
  name          = "${local.name_prefix}-api"
  protocol_type = "HTTP"
  description   = "API de adquirencia de FinCorp Payments"

  cors_configuration {
    # La web de pago QR se sirve desde S3, otro origen. Sin CORS el navegador
    # bloquea la llamada.
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type", "idempotency-key", "authorization"]
    max_age       = 300
  }

  tags = { Name = "${local.name_prefix}-api" }
}

# El VPC Link deja que API Gateway alcance un ALB INTERNO. Es lo que hace que la
# API sea la unica entrada: con un ALB publico, cualquiera podria llamarlo
# directamente y saltarse el throttling y los logs de API Gateway.
resource "aws_apigatewayv2_vpc_link" "main" {
  name               = "${local.name_prefix}-vpclink"
  security_group_ids = [aws_security_group.vpc_link.id]
  subnet_ids         = aws_subnet.private[*].id

  tags = { Name = "${local.name_prefix}-vpclink" }
}

resource "aws_apigatewayv2_integration" "backend" {
  api_id             = aws_apigatewayv2_api.main.id
  integration_type   = "HTTP_PROXY"
  integration_uri    = aws_lb_listener.backend.arn
  integration_method = "ANY"

  connection_type = "VPC_LINK"
  connection_id   = aws_apigatewayv2_vpc_link.main.id

  payload_format_version = "1.0"
  timeout_milliseconds   = 29000
}

# Ruta comodin: el enrutado real lo hace FastAPI. Meter cada ruta aqui duplicaria
# la definicion en dos sitios que pueden divergir.
resource "aws_apigatewayv2_route" "proxy" {
  api_id    = aws_apigatewayv2_api.main.id
  route_key = "ANY /{proxy+}"
  target    = "integrations/${aws_apigatewayv2_integration.backend.id}"
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/aws/apigateway/${local.name_prefix}"
  retention_in_days = 7

  tags = { Name = "${local.name_prefix}-api-logs" }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.main.id
  name        = "$default"
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.api.arn
    # El log incluye la latencia de integracion: es lo que se mira cuando una
    # autorizacion tarda mas de lo que el POS espera.
    format = jsonencode({
      requestId          = "$context.requestId"
      ip                 = "$context.identity.sourceIp"
      requestTime        = "$context.requestTime"
      httpMethod         = "$context.httpMethod"
      routeKey           = "$context.routeKey"
      status             = "$context.status"
      responseLength     = "$context.responseLength"
      integrationLatency = "$context.integrationLatency"
    })
  }

  default_route_settings {
    # Throttling: la API no tiene autenticacion, asi que este es el limite que
    # impide que alguien la use de forma abusiva. Mas que suficiente para un POS.
    throttling_burst_limit = 50
    throttling_rate_limit  = 20
  }

  tags = { Name = "${local.name_prefix}-api-stage" }
}
