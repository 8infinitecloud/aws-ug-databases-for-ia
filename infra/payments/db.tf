# =============================================================================
# La base transaccional "on-premises": PostgreSQL 16 sobre EC2
# =============================================================================
# Antes era Aurora Serverless v2. Ahora simula lo que tiene la mayoria de bancos
# y adquirentes de la region: el core corriendo en un servidor propio, que nadie
# va a migrar este trimestre. La nube entra por los datos, no por el core.
#
# Y es lo que justifica DMS en el Lab 1. Sobre un PostgreSQL autogestionado no
# hay integraciones zero-ETL: las opciones reales para sacar los cambios son DMS
# o Debezium. Con DMS leyendo el WAL (CDC), cada pago de la audiencia aparece en
# el lago como un delta, incluido el cobro que cambia de estado despues.
#
# Lo que este fichero tiene que garantizar para que eso funcione, y que en Aurora
# daba el parameter group:
#
#   wal_level = logical          sin esto no hay CDC
#   max_slot_wal_keep_size       el TOPE de WAL que un slot puede retener. Si DMS
#                                se para y nadie borra su slot, PostgreSQL guarda
#                                el WAL para el hasta llenar el disco y tumbar la
#                                base. Es el fallo clasico del CDC sobre
#                                PostgreSQL, y aqui seria en vivo.
#   pg_hba con replication       DMS abre una conexion de replicacion, no solo SQL
#
# Va en la subred PUBLICA, con IP publica solo de salida: la misma concesion que
# ya hace Fargate. En la privada no podria instalar paquetes ni hablar con SSM
# sin un NAT (~1 USD/dia) o tres endpoints de interfaz solo para SSM. El security
# group no abre NINGUN puerto a internet: 5432 solo desde el backend y desde la
# VPC (DMS), y ni siquiera SSH: se entra por Session Manager.

data "aws_ssm_parameter" "al2023_arm64" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"
}

locals {
  # IP privada FIJA. El secreto lleva el host, y la instancia lee el secreto al
  # arrancar: con la IP asignada por AWS habria un ciclo (el secreto depende de
  # la instancia y la instancia del secreto). Con una IP conocida de antemano,
  # no lo hay. La .50 de la primera subred publica, lejos de las que reserva AWS.
  db_private_ip = cidrhost(aws_subnet.public[0].cidr_block, 50)
  schema_files  = fileset("${path.module}/../../payments/schema", "0*.sql")
}

# ── Los ficheros del esquema ─────────────────────────────────────────────────
# La instancia los descarga y los aplica en orden al arrancar. Antes habia que
# hacerlo a mano por un tunel SSM despues de cada despliegue; ahora el core nace
# con su esquema, su seed y su cartera.
resource "aws_s3_bucket" "artifacts" {
  bucket_prefix = "${local.name_prefix}-artifacts-"
  force_destroy = true # el lab se destruye entero
  tags          = { Name = "${local.name_prefix}-artifacts" }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_object" "schema" {
  for_each = local.schema_files
  bucket   = aws_s3_bucket.artifacts.id
  key      = "schema/${each.value}"
  source   = "${path.module}/../../payments/schema/${each.value}"
  # Cambia el SQL, cambia el objeto. Para reaplicarlo sobre una base viva:
  # scripts/apply_schema.sh.
  etag = filemd5("${path.module}/../../payments/schema/${each.value}")
}

# ── La instancia ─────────────────────────────────────────────────────────────
resource "aws_iam_role" "db" {
  name = "${local.name_prefix}-db"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# Session Manager: la unica forma de entrar. Sin SSH, sin clave, sin puerto 22.
resource "aws_iam_role_policy_attachment" "db_ssm" {
  role       = aws_iam_role.db.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_role_policy" "db" {
  name = "DbBootstrap"
  role = aws_iam_role.db.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "LeerSuContrasena"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.db.arn
      },
      {
        Sid      = "LeerElEsquema"
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [aws_s3_bucket.artifacts.arn, "${aws_s3_bucket.artifacts.arn}/schema/*"]
      },
      {
        # Las metricas del WAL retenido y del disco (ver user_data). CloudWatch
        # no admite acotar PutMetricData por recurso; se acota por namespace.
        Sid      = "PublicarSusMetricas"
        Effect   = "Allow"
        Action   = ["cloudwatch:PutMetricData"]
        Resource = "*"
        Condition = {
          StringEquals = { "cloudwatch:namespace" = local.db_metrics_namespace }
        }
      },
    ]
  })
}

resource "aws_iam_instance_profile" "db" {
  name = "${local.name_prefix}-db"
  role = aws_iam_role.db.name
}

locals {
  db_metrics_namespace = "FinCorp/Postgres"
}

resource "aws_instance" "db" {
  ami                    = data.aws_ssm_parameter.al2023_arm64.value
  instance_type          = var.db_instance_type
  subnet_id              = aws_subnet.public[0].id
  private_ip             = local.db_private_ip
  vpc_security_group_ids = [aws_security_group.db.id]
  iam_instance_profile   = aws_iam_instance_profile.db.name
  # Las t4g ya lo son por defecto y sin coste: declararlo cierra un hallazgo de
  # checkov (CKV_AWS_135) que no era una concesion.
  ebs_optimized = true

  # IMDSv2 obligatorio: el v1 deja leer las credenciales del rol a cualquiera
  # que consiga una SSRF en algo que corra aqui.
  metadata_options {
    http_tokens   = "required"
    http_endpoint = "enabled"
  }

  root_block_device {
    volume_type           = "gp3"
    volume_size           = var.db_volume_gb
    encrypted             = true
    delete_on_termination = true
  }

  user_data = templatefile("${path.module}/db_user_data.sh.tftpl", {
    region            = var.aws_region
    secret_arn        = aws_secretsmanager_secret.db.arn
    db_name           = var.db_name
    db_user           = var.db_username
    vpc_cidr          = aws_vpc.main.cidr_block
    schema_bucket     = aws_s3_bucket.artifacts.id
    wal_keep_size     = var.db_max_slot_wal_keep_size
    metrics_namespace = local.db_metrics_namespace
  })
  # Cambiar el script de arranque NO recrea la base: se perderian los datos.
  # Para cambios de esquema esta scripts/apply_schema.sh.
  user_data_replace_on_change = false

  # El secreto y el esquema tienen que existir cuando la instancia arranca y los
  # lee, no solo cuando Terraform termina.
  depends_on = [aws_secretsmanager_secret_version.db, aws_s3_object.schema]

  tags = { Name = "${local.name_prefix}-db-onprem" }
}

# ── El secreto ────────────────────────────────────────────────────────────────
# Sin name fijo: Secrets Manager retiene los borrados y un redeploy rapido tras
# el cleanup chocaria con un nombre "ya existente".
resource "aws_secretsmanager_secret" "db" {
  name_prefix             = "${local.name_prefix}-db-"
  description             = "Credenciales del PostgreSQL del core (EC2 on-prem simulado)"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "db" {
  secret_id = aws_secretsmanager_secret.db.id
  # El formato que espera el backend (app/config.py lee la clave "password") y
  # tambien el que DMS entiende.
  secret_string = jsonencode({
    username = var.db_username
    password = var.db_password
    host     = local.db_private_ip
    port     = 5432
    dbname   = var.db_name
    engine   = "postgres"
  })
}

# ── Alarmas: el replication slot no puede tumbar la base ─────────────────────
# Las metricas las publica la propia instancia cada minuto (user_data). Sin
# accion (SNS): en un lab nadie esta de guardia, pero el estado ALARM en la
# consola es lo primero que hay que mirar si el Lab 1 deja de recibir deltas.
resource "aws_cloudwatch_metric_alarm" "wal_retained" {
  alarm_name          = "${local.name_prefix}-wal-retenido"
  alarm_description   = "Un slot de replicacion retiene demasiado WAL: DMS parado o atrasado. Borra el slot o reanuda la tarea."
  namespace           = local.db_metrics_namespace
  metric_name         = "RetainedWalBytes"
  dimensions          = { Database = var.db_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 5
  threshold           = 1073741824 # 1 GiB
  comparison_operator = "GreaterThanThreshold"
  # Sin datos = sin estado, no alarma: los primeros minutos tras el arranque la
  # instancia todavia no ha publicado nada.
  treat_missing_data = "missing"
}

resource "aws_cloudwatch_metric_alarm" "disk" {
  alarm_name          = "${local.name_prefix}-disco"
  alarm_description   = "El disco de PostgreSQL pasa del 80%."
  namespace           = local.db_metrics_namespace
  metric_name         = "DiskUsedPercent"
  dimensions          = { Database = var.db_name }
  statistic           = "Maximum"
  period              = 60
  evaluation_periods  = 3
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "missing"
}
