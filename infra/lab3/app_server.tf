# =============================================================================
# App Server EC2 - sirve la UI de Streamlit
# =============================================================================

resource "aws_iam_role" "app" {
  name = "${var.project_name}-app-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${var.project_name}-app-role" }
}

# Sin esto el EC2 no aparece en SSM y deploy-app.sh no puede ejecutar nada:
# todo el despliegue va por Run Command, no por SSH.
resource "aws_iam_role_policy_attachment" "app_ssm" {
  role       = aws_iam_role.app.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "app" {
  name = "RAGLabPolicy"
  role = aws_iam_role.app.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Acotado a los modelos que el lab usa, en vez de Resource = "*".
        # Si cambias BEDROCK_LLM_MODEL en el .env, anade su id a la variable
        # bedrock_model_ids o la llamada fallara con AccessDenied.
        Sid    = "BedrockInvoke"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:InvokeModelWithResponseStream",
        ]
        Resource = [
          for id in var.bedrock_model_ids :
          "arn:aws:bedrock:${var.aws_region}::foundation-model/${id}"
        ]
      },
      {
        Sid    = "UserMemoryTable"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:UpdateItem",
          "dynamodb:Query",
        ]
        Resource = aws_dynamodb_table.user_memory.arn
      },
      {
        Sid    = "DocumentsBucket"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.documents.arn,
          "${aws_s3_bucket.documents.arn}/*",
        ]
      },
      {
        # Solo lectura, y solo de lo que convierte en hechos el cierre de la
        # demo (ingest_from_curated.py): la zona curada, el informe de calidad
        # y la telemetria. Ni raw/acquiring/ ni rejected/.
        Sid    = "DataLakeRead"
        Effect = "Allow"
        Action = ["s3:GetObject"]
        Resource = [for p in ["curated", "quality-reports", "raw/telemetry"] :
        "arn:aws:s3:::${local.datalake_bucket}/${p}/*"]
      },
      {
        Sid      = "DataLakeList"
        Effect   = "Allow"
        Action   = ["s3:ListBucket"]
        Resource = "arn:aws:s3:::${local.datalake_bucket}"
      },
      {
        Sid      = "AuroraCredentials"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.aurora.arn
      },
    ]
  })
}

resource "aws_iam_instance_profile" "app" {
  name = "${var.project_name}-app-profile"
  role = aws_iam_role.app.name
}

# AMI mas reciente de Amazon Linux 2023, resuelto en cada apply.
data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

resource "aws_instance" "app" {
  ami           = data.aws_ssm_parameter.al2023.value
  instance_type = "t3.medium"
  subnet_id     = aws_subnet.public.id

  vpc_security_group_ids = [
    aws_security_group.app.id,       # salida + acceso a Aurora y Redis
    aws_security_group.streamlit.id, # puerto 8501 abierto
  ]

  iam_instance_profile = aws_iam_instance_profile.app.name

  # IMDSv2 obligatorio. Esta instancia lleva un rol con acceso a Bedrock, S3 y
  # Secrets Manager: con IMDSv1 basta una SSRF en la app para robar esas
  # credenciales. Exigir token lo corta y no cuesta nada.
  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  root_block_device {
    encrypted   = true
    volume_size = 20
    volume_type = "gp3"
  }

  # El UserData solo prepara el entorno y escribe el .env con los endpoints.
  # El codigo de la app lo sube deploy-app.sh via SSM.
  user_data = templatefile("${path.module}/user_data.sh.tftpl", {
    region            = var.aws_region
    aurora_host       = aws_rds_cluster.aurora.endpoint
    aurora_secret_arn = aws_secretsmanager_secret.aurora.arn
    redis_host        = aws_elasticache_cluster.redis.cache_nodes[0].address
    bucket_name       = aws_s3_bucket.documents.id
    dynamodb_table    = aws_dynamodb_table.user_memory.name
  })

  tags = { Name = "${var.project_name}-app-server" }

  # El .env necesita los endpoints, asi que nada de esto puede crearse antes.
  depends_on = [
    aws_rds_cluster_instance.aurora,
    aws_elasticache_cluster.redis,
    aws_secretsmanager_secret_version.aurora,
  ]
}

locals {
  datalake_bucket = coalesce(var.datalake_bucket,
  "fincorp-datalake-${data.aws_caller_identity.current.account_id}")
}
