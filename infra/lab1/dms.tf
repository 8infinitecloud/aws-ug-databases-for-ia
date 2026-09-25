# =============================================================================
# AWS DMS - Full Load + CDC de MySQL a S3
# =============================================================================
# Es la pieza que responde a "como entran los datos a la plataforma": sin tocar
# el sistema origen, sin escribir un ETL a mano.

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

# Permiso acotado al bucket del lab, en vez de AmazonS3FullAccess.
resource "aws_iam_role_policy" "dms_s3" {
  name = "DMSDataLakeAccess"
  role = aws_iam_role.dms.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:ListBucket",
        "s3:GetBucketLocation",
      ]
      Resource = [
        aws_s3_bucket.datalake.arn,
        "${aws_s3_bucket.datalake.arn}/*",
      ]
    }]
  })
}

# DMS crea y destruye ENIs en las subnets privadas para alcanzar RDS.
resource "aws_iam_role_policy" "dms_vpc" {
  name = "DMSVPCManagement"
  role = aws_iam_role.dms.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "ec2:CreateNetworkInterface",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DeleteNetworkInterface",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:ModifyNetworkInterfaceAttribute",
      ]
      Resource = "*"
    }]
  })
}

resource "aws_dms_replication_subnet_group" "main" {
  replication_subnet_group_id          = "${var.project_name}-${var.environment}-dms-subnet"
  replication_subnet_group_description = "Subnets para DMS Replication Instance"
  subnet_ids                           = aws_subnet.private[*].id

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

resource "aws_dms_endpoint" "source_mysql" {
  endpoint_id   = "${var.project_name}-${var.environment}-source-mysql"
  endpoint_type = "source"
  engine_name   = "mysql"

  server_name   = aws_db_instance.mysql.address
  port          = 3306
  database_name = var.db_name
  username      = var.db_username
  password      = var.db_password

  tags = { Name = "${var.project_name}-${var.environment}-source-mysql" }
}

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

    # Sin columna de operacion en el full load: el Lab 2 lee el CSV como una
    # tabla normal, no como un log de cambios.
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
  source_endpoint_arn      = aws_dms_endpoint.source_mysql.endpoint_arn
  target_endpoint_arn      = aws_dms_endpoint.target_s3.endpoint_arn
  migration_type           = "full-load-and-cdc"

  table_mappings = jsonencode({
    rules = [
      {
        rule-type = "selection"
        rule-id   = "1"
        rule-name = "include-transactions"
        object-locator = {
          schema-name = var.db_name
          table-name  = "transactions"
        }
        rule-action = "include"
      },
      {
        rule-type = "selection"
        rule-id   = "2"
        rule-name = "include-customers"
        object-locator = {
          schema-name = var.db_name
          table-name  = "customers"
        }
        rule-action = "include"
      },
    ]
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
      TargetTablePrepMode             = "DROP_AND_CREATE"
      CreatePkAfterFullLoad           = false
      StopTaskCachedChangesApplied    = false
      StopTaskCachedChangesNotApplied = false
      MaxFullLoadSubTasks             = 8
      TransactionConsistencyTimeout   = 600
      CommitRate                      = 50000
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

  # La tarea se arranca a mano durante la preparacion del demo, para controlar
  # el momento exacto (ver docs/demo-runbook.md).
  start_replication_task = false

  tags = { Name = "${var.project_name}-${var.environment}-task" }
}
