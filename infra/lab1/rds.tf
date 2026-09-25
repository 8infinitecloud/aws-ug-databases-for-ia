# =============================================================================
# RDS MySQL - el sistema origen de FinCorp
# =============================================================================

resource "aws_db_subnet_group" "mysql" {
  name        = "${var.project_name}-${var.environment}-db-subnet-group"
  description = "Subnets privadas para RDS MySQL"
  subnet_ids  = aws_subnet.private[*].id

  tags = { Name = "${var.project_name}-${var.environment}-db-subnet-group" }
}

# DMS lee el binlog para hacer CDC. Sin binlog en formato ROW no hay captura de
# cambios, y estos parametros no se pueden fijar en la instancia: van en un
# parameter group.
resource "aws_db_parameter_group" "mysql" {
  name        = "${var.project_name}-${var.environment}-mysql-pg"
  family      = "mysql8.0"
  description = "Habilita binlog ROW para CDC de AWS DMS"

  parameter {
    name  = "binlog_format"
    value = "ROW"
  }

  parameter {
    name  = "binlog_row_image"
    value = "Full"
  }

  parameter {
    name  = "binlog_checksum"
    value = "NONE"
  }

  tags = { Name = "${var.project_name}-${var.environment}-mysql-pg" }
}

resource "aws_db_instance" "mysql" {
  identifier     = "${var.project_name}-${var.environment}-mysql"
  engine         = "mysql"
  engine_version = var.mysql_version
  instance_class = "db.t3.micro"

  db_name  = var.db_name
  username = var.db_username
  password = var.db_password

  allocated_storage = 20
  storage_type      = "gp2"
  multi_az          = false

  db_subnet_group_name   = aws_db_subnet_group.mysql.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  parameter_group_name   = aws_db_parameter_group.mysql.name

  # CDC exige backups automaticos activos: con retencion 0 MySQL no conserva el
  # binlog y la tarea DMS falla en cuanto pasa del full load.
  backup_retention_period = 1

  # Es un lab: se crea y se destruye muchas veces. Sin snapshot final y sin
  # proteccion, para que el cleanup no se quede colgado.
  deletion_protection = false
  skip_final_snapshot = true

  tags = { Name = "${var.project_name}-${var.environment}-mysql" }
}

resource "aws_secretsmanager_secret" "rds" {
  name        = "${var.project_name}/${var.environment}/rds-mysql"
  description = "Credenciales RDS MySQL para FinCorp Data Platform"

  # Sin esto AWS retiene el secret 30 dias y el siguiente apply choca con un
  # nombre "ya existente" que no se ve por ningun lado.
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "rds" {
  secret_id = aws_secretsmanager_secret.rds.id

  secret_string = jsonencode({
    username = var.db_username
    password = var.db_password
    host     = aws_db_instance.mysql.address
    port     = "3306"
    dbname   = var.db_name
    engine   = "mysql"
  })
}
