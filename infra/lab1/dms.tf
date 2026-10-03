# =============================================================================
# AWS DMS — del core "on-premises" al data lake, con CDC
# =============================================================================
# Replica el PostgreSQL del core (EC2, infra/payments) hacia raw/acquiring/ en
# S3: primero una foto completa (full load) y despues CADA CAMBIO, leyendo el
# WAL (CDC). Es lo que hace que un pago de la audiencia aparezca en el lago
# segundos despues, y que el mismo cobro llegue dos veces: como I (insert) al
# crearse y como U (update) al capturarse.
#
# DMS NO TRANSFORMA: replica. Las tablas llegan a raw/ tal como estan en el
# origen, una carpeta por tabla, con dos columnas mas: Op (I/U/D) y
# dms_timestamp. El JOIN que las convierte en una fila por transaccion es
# trabajo de Glue (Lab 2). Raw es lo que llego; curated es lo que hicimos.
#
# Por que DMS y no Glue con JDBC: Glue incremental pregunta "que hay nuevo" con
# una consulta, y asi no ve ni las actualizaciones ni los borrados. En pagos el
# cambio de estado ES el dato: la captura, la devolucion, el contracargo. Y
# sobre un PostgreSQL autogestionado no hay zero-ETL. Ver ADR-024.
#
# Antes este modulo hacia full load de una tabla espejo de una vista, porque DMS
# no puede leer vistas (ADR-019). Con CDC sobre las tablas base, la vista sigue
# existiendo como contrato legible, pero DMS ya no la necesita.

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

# DMS crea y destruye ENIs en las subnets para alcanzar la base del core.
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
# DMS vive en las subnets privadas de payments: alcanza la base del core por la
# red interna de la VPC, sin exponerla, y S3 por el gateway endpoint.
resource "aws_security_group" "dms" {
  name        = "${var.project_name}-${var.environment}-dms-sg"
  description = "DMS - salida a la base del core y a S3"
  vpc_id      = local.vpc_id

  egress {
    description = "PostgreSQL hacia el core, dentro de la VPC"
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
    # Que el log group exista antes: si lo crea DMS primero, el apply siguiente
    # falla con ResourceAlreadyExistsException.
    aws_cloudwatch_log_group.dms_tasks,
  ]

  replication_subnet_group_id          = "${var.project_name}-${var.environment}-dms-subnet"
  replication_subnet_group_description = "Subnets privadas del core para DMS"
  subnet_ids                           = local.private_subnet_ids

  tags = { Name = "${var.project_name}-${var.environment}-dms-subnet" }
}

resource "aws_dms_replication_instance" "main" {
  replication_instance_id    = "${var.project_name}-${var.environment}-ri"
  replication_instance_class = var.dms_instance_class
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

# ── Origen: el PostgreSQL del core ───────────────────────────────────────────
resource "aws_dms_endpoint" "source_core" {
  endpoint_id   = "${var.project_name}-${var.environment}-source-core"
  endpoint_type = "source"
  engine_name   = "postgres"
  server_name   = local.db_host
  port          = 5432
  database_name = local.db_name

  # Las credenciales salen del secreto que creo payments: este modulo no recibe
  # ninguna contrasena como variable.
  username = local.db_creds["username"]
  password = local.db_creds["password"]

  postgres_settings {
    # El plugin de decodificacion logica. Viene en postgresql16-contrib, que el
    # arranque de la instancia instala por esto.
    plugin_name = "test_decoding"
    # Sin capturar DDL: DMS no crea su event trigger en el core. El esquema lo
    # cambia payments/schema/, no la replicacion.
    capture_ddls = false
    # El latido: DMS escribe en una tabla propia cada minuto para que el slot
    # avance aunque no haya pagos. Sin el, en una base con poca actividad en las
    # tablas replicadas el slot se queda quieto y retiene WAL de todo lo demas.
    heartbeat_enable    = true
    heartbeat_frequency = 1
  }

  tags = { Name = "${var.project_name}-${var.environment}-source-core" }
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

    # Parquet y no CSV. Con CSV ya pasaron dos cosas: el fichero salia sin
    # cabecera (Glue tomaba la primera fila como encabezado) y todo llegaba como
    # texto. Parquet lleva su esquema: enteros, timestamps y decimales llegan
    # como tales, y Athena puede consultar raw/ directamente en la demo.
    data_format                      = "parquet"
    parquet_version                  = "parquet-2-0"
    enable_statistics                = true
    parquet_timestamp_in_millisecond = true
    compression_type                 = "GZIP"

    # La columna Op TAMBIEN en el full load (como I). Si no, la foto inicial y
    # los deltas tendrian esquemas distintos en la misma carpeta, y Glue tendria
    # que tratar dos formatos.
    include_op_for_full_load = true
    timestamp_column_name    = "dms_timestamp"

    # Los deltas en carpetas por dia: raw/acquiring/charges/2026/10/02/. Sin
    # esto, una demo de una hora deja cientos de ficheros en una sola carpeta.
    date_partition_enabled  = true
    date_partition_sequence = "YYYYMMDD"

    # Un fichero de cambios cada 30 s como mucho: es lo que tarda un pago de la
    # sala en aparecer en el lago. El tamano minimo es alto a proposito: con el
    # volumen de una demo nunca se alcanza, y manda el intervalo.
    cdc_max_batch_interval = 30
    cdc_min_file_size      = 32000
  }

  tags = { Name = "${var.project_name}-${var.environment}-target-s3" }

  depends_on = [aws_s3_bucket_policy.datalake]
}

locals {
  # Lo que SI se replica: el negocio del adquirente.
  replicated_tables = [
    "merchants", "terminals", "payment_intents", "charges",
    "balance_transactions", "refunds", "disputes", "payouts", "webhook_events",
  ]
  # Lo que NO, y por que. Un data lake es un sitio donde mucha gente lee: lo que
  # no debe leer nadie no puede llegar ahi.
  #   merchant_api_keys     hashes de credenciales
  #   webhook_endpoints     el secreto con el que firmamos los webhooks
  #   payment_methods       tokens de instrumentos de pago: alcance PCI
  #   idempotency_keys      cuerpos de respuesta completos, operacion pura
  #   webhook_deliveries    reintentos de entrega, operacion pura
  #   demo_catalog          no es del adquirente
}

resource "aws_dms_replication_task" "main" {
  replication_task_id      = "${var.project_name}-${var.environment}-task"
  replication_instance_arn = aws_dms_replication_instance.main.replication_instance_arn
  source_endpoint_arn      = aws_dms_endpoint.source_core.endpoint_arn
  target_endpoint_arn      = aws_dms_endpoint.target_s3.endpoint_arn

  migration_type = "full-load-and-cdc"

  table_mappings = jsonencode({
    rules = concat(
      [for i, t in local.replicated_tables : {
        rule-type      = "selection"
        rule-id        = tostring(i + 1)
        rule-name      = "incluir-${t}"
        object-locator = { schema-name = var.source_schema, table-name = t }
        rule-action    = "include"
      }],
      [{
        # La cuenta bancaria del comercio se queda en el core. El lago no
        # necesita saber a donde se liquida para analizar cuanto se liquida.
        rule-type   = "transformation"
        rule-id     = "100"
        rule-name   = "sin-cuenta-de-liquidacion"
        rule-target = "column"
        object-locator = {
          schema-name = var.source_schema
          table-name  = "merchants"
          column-name = "settlement_account"
        }
        rule-action = "remove-column"
      }],
    )
  })

  replication_task_settings = jsonencode({
    TargetMetadata = {
      TargetSchema = ""
      # LOBs ACTIVADOS, y es la otra leccion del despliegue anterior. Para DMS,
      # el text y el jsonb de PostgreSQL son LOBs; con SupportLobs = false los
      # DESCARTA sin avisar. Asi se perdieron risk_level, payment_method y
      # status la primera vez. Aqui line_items, metadata o el payload de los
      # eventos desaparecerian igual. Modo limitado a 32 KB: ningun campo de
      # este modelo se acerca, y es mucho mas rapido que el modo completo.
      SupportLobs        = true
      FullLobMode        = false
      LimitedSizeLobMode = true
      LobMaxSize         = 32
    }
    FullLoadSettings = {
      TargetTablePrepMode = "DROP_AND_CREATE"
      MaxFullLoadSubTasks = 8
      CommitRate          = 50000
    }
    Logging = {
      EnableLogging = true
      LogComponents = [
        { Id = "SOURCE_UNLOAD", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "SOURCE_CAPTURE", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "TARGET_LOAD", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "TARGET_APPLY", Severity = "LOGGER_SEVERITY_DEFAULT" },
        { Id = "TASK_MANAGER", Severity = "LOGGER_SEVERITY_DEFAULT" },
      ]
    }
  })

  # Se arranca durante la preparacion de la demo (labs/01/scripts/setup.sh):
  # desde ese momento el slot existe y retiene WAL, asi que no conviene que
  # arranque solo con el apply y se quede corriendo sin nadie mirando.
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

# =============================================================================
# El log group de la tarea DMS
# =============================================================================
# Lo crea DMS por su cuenta al escribir los logs de la tarea, con retencion
# infinita y fuera del control de Terraform. `destroy` lo dejaba huerfano: los
# logs siguen ahi, siguen cobrando, y el siguiente despliegue los mezcla con los
# del anterior.
#
# El nombre no es libre: DMS escribe exactamente en
# dms-tasks-<identificador-de-la-instancia>, asi que tiene que coincidir.
# Declararlo aqui lo convierte en un recurso mas, con retencion y que se va con
# el resto.

resource "aws_cloudwatch_log_group" "dms_tasks" {
  name              = "dms-tasks-${var.project_name}-${var.environment}-ri"
  retention_in_days = 7

  tags = { Name = "${var.project_name}-${var.environment}-dms-logs" }
}
