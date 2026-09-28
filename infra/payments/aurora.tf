# =============================================================================
# Aurora PostgreSQL Serverless v2 — la base transaccional
# =============================================================================
# Serverless v2 y no provisionado por una razon concreta: entre ensayos la demo
# esta inactiva, y Serverless baja a su capacidad minima sin intervencion. Un
# db.r6g.large costaria lo mismo encendido que en uso.

resource "aws_db_subnet_group" "aurora" {
  name        = "${local.name_prefix}-aurora-subnets"
  description = "Subnets privadas para Aurora"
  subnet_ids  = aws_subnet.private[*].id

  tags = { Name = "${local.name_prefix}-aurora-subnets" }
}

# Replicacion logica: es lo que DMS necesita para leer los cambios de
# PostgreSQL. Es el equivalente al binlog de MySQL, y sin esto el CDC del Lab 1
# no funciona.
#
# Va en el parameter group del CLUSTER, no de la instancia: rds.logical_replication
# es un parametro estatico a nivel de cluster.
resource "aws_rds_cluster_parameter_group" "aurora" {
  name        = "${local.name_prefix}-aurora-cluster-pg"
  family      = "aurora-postgresql16"
  description = "Habilita replicacion logica para el CDC de AWS DMS"

  parameter {
    name         = "rds.logical_replication"
    value        = "1"
    apply_method = "pending-reboot"
  }

  # Aurora exige al menos un slot y un walsender por cada tarea de DMS.
  parameter {
    name         = "max_replication_slots"
    value        = "10"
    apply_method = "pending-reboot"
  }

  parameter {
    name         = "max_wal_senders"
    value        = "10"
    apply_method = "pending-reboot"
  }

  # Registrar las sentencias lentas: en un sistema de pagos, una autorizacion que
  # tarda es un problema de negocio, no solo de rendimiento.
  parameter {
    name  = "log_min_duration_statement"
    value = "1000"
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-aurora-cluster-pg" }
}

resource "aws_rds_cluster" "aurora" {
  cluster_identifier = "${local.name_prefix}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned" # obligatorio para Serverless v2
  engine_version     = var.aurora_engine_version

  database_name   = var.db_name
  master_username = var.db_username
  master_password = var.db_password

  db_subnet_group_name            = aws_db_subnet_group.aurora.name
  vpc_security_group_ids          = [aws_security_group.aurora.id]
  db_cluster_parameter_group_name = aws_rds_cluster_parameter_group.aurora.name

  storage_encrypted = true

  # Es un lab que se crea y destruye: sin proteccion ni snapshot final, para que
  # el cleanup no se quede a medias con el coste corriendo.
  deletion_protection = false
  skip_final_snapshot = true

  # DMS necesita backups activos para retener los WAL, igual que MySQL necesita
  # backups para retener el binlog. Con 0 el CDC falla tras el full load.
  backup_retention_period = 1

  serverlessv2_scaling_configuration {
    min_capacity = var.aurora_min_acu
    max_capacity = var.aurora_max_acu
  }

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = { Name = "${local.name_prefix}-aurora" }
}

resource "aws_rds_cluster_instance" "aurora" {
  identifier         = "${local.name_prefix}-aurora-1"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine
  engine_version     = aws_rds_cluster.aurora.engine_version

  publicly_accessible = false

  tags = { Name = "${local.name_prefix}-aurora-1" }
}

# ── El secreto ────────────────────────────────────────────────────────────────
# Sin name fijo: Secrets Manager retiene los borrados y un redeploy rapido tras
# el cleanup chocaria con un nombre "ya existente".
resource "aws_secretsmanager_secret" "aurora" {
  name_prefix             = "${local.name_prefix}-aurora-"
  description             = "Credenciales de Aurora para el backend de adquirencia"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "aurora" {
  secret_id = aws_secretsmanager_secret.aurora.id

  # El formato que espera el backend (app/config.py lee la clave "password") y
  # tambien el que DMS entiende.
  secret_string = jsonencode({
    username = var.db_username
    password = var.db_password
    host     = aws_rds_cluster.aurora.endpoint
    port     = 5432
    dbname   = var.db_name
    engine   = "postgres"
  })
}
