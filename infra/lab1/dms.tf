# =============================================================================
# AWS DMS — del core transaccional al data lake
# =============================================================================
# Replica desde Aurora PostgreSQL (infra/payments) hacia la zona raw/ de S3.
#
# POR QUE FULL LOAD Y NO CDC, que es la decision importante de este modulo:
#
# DMS **no puede hacer CDC sobre una vista**. El CDC lee el WAL, y una vista no
# tiene WAL propio: no es una tabla, es una consulta. El full load si puede
# seleccionar de una vista, porque es un SELECT normal.
#
# Y la vista es lo que queremos replicar: entrega el dato aplanado y aisla el
# schema interno del consumidor. Renunciar a ella para tener CDC significaria
# replicar charges, merchants, terminals y balance_transactions por separado y
# rehacer el JOIN en Glue.
#
# La salida es que la plataforma tiene DOS velocidades de ingesta, y cada una usa
# el mecanismo que le corresponde:
#
#   HISTORICO      Aurora ──DMS full load──▶ S3 raw/       (este modulo)
#   TIEMPO REAL    Terminal ──IoT Core──▶ Firehose ──▶ S3  (ver la propuesta
#                                                            del Smart Coffee)
#
# No es una limitacion disfrazada de virtud: es como funcionan las plataformas de
# datos de verdad. El batch carga la historia; el streaming lleva lo que pasa
# ahora. Intentar que un solo mecanismo haga las dos cosas es el error comun.

resource "aws_iam_role" "dms" {
  name = "${var.project_name}-${var.environment}-dms-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dms.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "${var.project_name}-${var.environment}-dms-role" }
}

# Acotado al bucket del lab, no AmazonS3FullAccess.
resource "aws_iam_role_policy" "dms_s3" {
  name = "DMSDataLakeAccess"
  role = aws_iam_role.dms.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:PutObject", "s3:DeleteObject", "s3:GetObject",
        "s3:ListBucket", "s3:GetBucketLocation",
      ]
      Resource = [
        aws_s3_bucket.datalake.arn,
        "${aws_s3_bucket.datalake.arn}/*",
      ]
    }]
  })
}

# DMS crea y destruye ENIs en las subnets para alcanzar Aurora.
resource "aws_iam_role_policy" "dms_vpc" {
  name = "DMSVPCManagement"
  role = aws_iam_role.dms.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ec2:CreateNetworkInterface", "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface", "ec2:DescribeVpcs", "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups", "ec2:ModifyNetworkInterfaceAttribute",
      ]
      Resource = "*"
    }]
  })
}

# ── Red: dentro de la VPC del core ────────────────────────────────────────────
# DMS vive en las subnets privadas de payments, que es la unica forma de alcanzar
# Aurora sin exponerla.
resource "aws_security_group" "dms" {
  name        = "${var.project_name}-${var.environment}-dms-sg"
  description = "DMS - salida a Aurora del core y a S3"
  vpc_id      = local.vpc_id

  egress {
    description = "PostgreSQL hacia Aurora, dentro de la VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [local.vpc_cidr]
  }

  egress {
    description = "S3 por HTTPS"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${var.project_name}-${var.environment}-dms-sg" }
}

resource "aws_dms_replication_subnet_group" "main" {
  # Sin esta dependencia Terraform crea el subnet group y el rol en paralelo, y
  # DMS falla porque el rol todavia no esta.
  depends_on = [
    aws_iam_role_policy_attachment.dms_vpc_role,
    aws_iam_role_policy_attachment.dms_cloudwatch_logs_role,
  ]

  replication_subnet_group_id          = "${var.project_name}-${var.environment}-dms-subnet"
  replication_subnet_group_description = "Subnets privadas del core para DMS"
  subnet_ids                           = local.private_subnet_ids

  tags = { Name = "${var.project_name}-${var.environment}-dms-subnet" }
}

resource "aws_dms_replication_instance" "main" {
  replication_instance_id    = "${var.project_name}-${var.environment}-ri"
  replication_instance_class = "dms.t3.medium"
  allocated_storage          = 50
  multi_az                   = false
  publicly_accessible        = false

  replication_subnet_group_id = aws_dms_replication_subnet_group.main.replication_subnet_group_id
  vpc_security_group_ids      = [aws_security_group.dms.id]

  tags = { Name = "${var.project_name}-${var.environment}-ri" }

  depends_on = [
    aws_iam_role_policy.dms_vpc,
    aws_iam_role_policy.dms_s3,
  ]
}

# ── Origen: Aurora PostgreSQL del core ────────────────────────────────────────
resource "aws_dms_endpoint" "source_aurora" {
  endpoint_id   = "${var.project_name}-${var.environment}-source-aurora"
  endpoint_type = "source"
  engine_name   = "postgres"

  server_name   = local.aurora_endpoint
  port          = 5432
  database_name = local.db_name
  # Las credenciales salen del secreto que creo payments: este modulo no recibe
  # ninguna contrasena como variable.
  username = local.aurora_creds["username"]
  password = local.aurora_creds["password"]

  tags = { Name = "${var.project_name}-${var.environment}-source-aurora" }
}

# ── Destino: la zona raw del data lake ────────────────────────────────────────
resource "aws_dms_endpoint" "target_s3" {
  endpoint_id   = "${var.project_name}-${var.environment}-target-s3"
  endpoint_type = "target"
  engine_name   = "s3"

  s3_settings {
    bucket_name             = aws_s3_bucket.datalake.id
    bucket_folder           = "raw"
    service_access_role_arn = aws_iam_role.dms.arn

    data_format      = "csv"
    encoding_type    = "plain"
    compression_type = "NONE"

    # Sin columna de operacion: en un full load no hay inserts/updates que
    # distinguir, y el Lab 2 lee el CSV como una tabla normal.
    include_op_for_full_load = false
    timestamp_column_name    = "dms_timestamp"

    csv_row_delimiter = "\\n"
    csv_delimiter     = ","
  }

  tags = { Name = "${var.project_name}-${var.environment}-target-s3" }

  depends_on = [aws_s3_bucket_policy.datalake]
}

resource "aws_dms_replication_task" "main" {
  replication_task_id      = "${var.project_name}-${var.environment}-task"
  replication_instance_arn = aws_dms_replication_instance.main.replication_instance_arn
  source_endpoint_arn      = aws_dms_endpoint.source_aurora.endpoint_arn
  target_endpoint_arn      = aws_dms_endpoint.target_s3.endpoint_arn

  # full-load y no full-load-and-cdc: ver la explicacion al principio del fichero.
  migration_type = "full-load"

  table_mappings = jsonencode({
    rules = [{
      rule-type = "selection"
      rule-id   = "1"
      rule-name = "include-transactions-view"
      object-locator = {
        schema-name = var.source_schema
        table-name  = var.source_object
      }
      rule-action = "include"
    }]
  })

  replication_task_settings = jsonencode({
    TargetMetadata = {
      TargetSchema       = ""
      SupportLobs        = false
      FullLobMode        = false
      LobChunkSize       = 64
      LimitedSizeLobMode = true
      LobMaxSize         = 32
    }
    FullLoadSettings = {
      TargetTablePrepMode           = "DROP_AND_CREATE"
      CreatePkAfterFullLoad         = false
      MaxFullLoadSubTasks           = 8
      TransactionConsistencyTimeout = 600
      CommitRate                    = 50000
    }
    Logging = {
      EnableLogging = true
      LogComponents = [
        { Id = "SOURCE_UNLOAD", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "TARGET_LOAD", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "TASK_MANAGER", Severity = "LOGGER_SEVERITY_DEFAULT" },
      ]
    }
  })

  # Se arranca a mano durante la preparacion del demo, para controlar el momento.
  start_replication_task = false

  tags = { Name = "${var.project_name}-${var.environment}-task" }
}

# =============================================================================
# El rol que DMS exige a nivel de cuenta
# =============================================================================
# Esto no se descubre con `terraform validate` ni con `plan`: el apply falla con
#
#   AccessDeniedFault: The IAM Role arn:aws:iam::<account>:role/dms-vpc-role
#   is not configured properly
#
# DMS busca un rol llamado exactamente `dms-vpc-role` para gestionar las ENIs de
# los subnet groups. No usa el rol que se le pasa al endpoint ni al task: ese
# nombre esta cableado en el servicio.
#
# Es un recurso de cuenta y no del lab, asi que `create_dms_vpc_role = false`
# permite reutilizar el que ya exista.

resource "aws_iam_role" "dms_vpc_role" {
  count = var.create_dms_vpc_role ? 1 : 0

  name = "dms-vpc-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dms.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "dms-vpc-role" }
}

resource "aws_iam_role_policy_attachment" "dms_vpc_role" {
  count = var.create_dms_vpc_role ? 1 : 0

  role       = aws_iam_role.dms_vpc_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonDMSVPCManagementRole"
}

# =============================================================================
# El rol de logs que DMS tampoco crea solo
# =============================================================================
# `EnableLogging = true` en los task settings NO basta. DMS escribe los logs de
# la tarea asumiendo un rol llamado exactamente `dms-cloudwatch-logs-role`, y si
# no existe **no avisa**: la tarea corre, el ajuste dice que loguea, y no hay
# log group en ninguna parte.
#
# Eso convierte cualquier fallo de la tarea en indiagnosticable, que es
# exactamente lo que paso: `No tables were found at task initialization` sin una
# sola linea de log que dijera que consulto DMS.

resource "aws_iam_role" "dms_cloudwatch_logs_role" {
  count = var.create_dms_vpc_role ? 1 : 0

  name = "dms-cloudwatch-logs-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "dms.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = { Name = "dms-cloudwatch-logs-role" }
}

resource "aws_iam_role_policy_attachment" "dms_cloudwatch_logs_role" {
  count = var.create_dms_vpc_role ? 1 : 0

  role       = aws_iam_role.dms_cloudwatch_logs_role[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonDMSCloudWatchLogsRole"
}
