# =============================================================================
# Permisos
# =============================================================================

data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "scorer" {
  name               = "${local.name_prefix}-scorer"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

data "aws_iam_policy_document" "scorer" {
  statement {
    sid    = "Logs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.scorer.arn}:*"]
  }

  statement {
    # Lo exige el modo Active de X-Ray. Sin esto la Lambda funciona pero no
    # aparece ninguna traza, que es peor que no tenerlo: parece que no traza.
    sid    = "Trazas"
    effect = "Allow"
    actions = [
      "xray:PutTraceSegments",
      "xray:PutTelemetryRecords",
    ]
    resources = ["*"] # X-Ray no admite recursos concretos en estas acciones
  }

  statement {
    sid    = "ContadoresYAlertas"
    effect = "Allow"
    actions = [
      "dynamodb:UpdateItem",
      "dynamodb:PutItem",
    ]
    # Solo esta tabla, y solo escribir: el scorer no lee nada mas ni borra nada.
    resources = [aws_dynamodb_table.velocity.arn]
  }
}

resource "aws_iam_role_policy" "scorer" {
  name   = "${local.name_prefix}-scorer"
  role   = aws_iam_role.scorer.id
  policy = data.aws_iam_policy_document.scorer.json
}

# ── IoT Core: escribe la telemetria en Firehose y en DynamoDB ───────────────
data "aws_iam_policy_document" "iot_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["iot.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "iot_rule" {
  name               = "${local.name_prefix}-iot-rule"
  assume_role_policy = data.aws_iam_policy_document.iot_assume.json
}

data "aws_iam_policy_document" "iot_rule" {
  statement {
    sid       = "TelemetriaAlLago"
    effect    = "Allow"
    actions   = ["firehose:PutRecord", "firehose:PutRecordBatch"]
    resources = [aws_kinesis_firehose_delivery_stream.telemetry.arn]
  }
  statement {
    sid       = "UltimoEstadoDelTerminal"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem"]
    resources = [aws_dynamodb_table.terminal_state.arn]
  }

  statement {
    # El error_action de la regla escribe aqui. Sin este permiso, un fallo de
    # entrega se pierde en silencio, que es el peor sitio donde perderlo.
    sid    = "RegistrarFallosDeEntrega"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.iot_errors.arn}:*"]
  }
}

resource "aws_iam_role_policy" "iot_rule" {
  name   = "${local.name_prefix}-iot-rule"
  role   = aws_iam_role.iot_rule.id
  policy = data.aws_iam_policy_document.iot_rule.json
}

# ── Firehose: escribe en el lago ─────────────────────────────────────────────
data "aws_iam_policy_document" "firehose_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["firehose.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_iam_role" "firehose" {
  name               = "${local.name_prefix}-firehose"
  assume_role_policy = data.aws_iam_policy_document.firehose_assume.json
}

data "aws_iam_policy_document" "firehose" {
  statement {
    effect = "Allow"
    actions = [
      "s3:AbortMultipartUpload",
      "s3:GetBucketLocation",
      "s3:GetObject",
      "s3:ListBucket",
      "s3:ListBucketMultipartUploads",
      "s3:PutObject",
    ]
    resources = [
      local.s3_bucket_arn,
      "${local.s3_bucket_arn}/*",
    ]
  }

  statement {
    effect    = "Allow"
    actions   = ["logs:PutLogEvents"]
    resources = ["${aws_cloudwatch_log_group.firehose.arn}:*"]
  }
}

resource "aws_iam_role_policy" "firehose" {
  name   = "${local.name_prefix}-firehose"
  role   = aws_iam_role.firehose.id
  policy = data.aws_iam_policy_document.firehose.json
}
