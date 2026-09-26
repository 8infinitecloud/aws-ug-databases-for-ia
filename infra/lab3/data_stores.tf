# =============================================================================
# Las tres capas de memoria + el vector store
# =============================================================================
#   Aurora pgvector -> conocimiento (RAG) y memoria semantica
#   Redis           -> memoria de sesion (capa 1)
#   DynamoDB        -> memoria persistente por usuario (capa 2)

# ── Aurora PostgreSQL Serverless v2 + pgvector ────────────────────────────────
resource "aws_db_subnet_group" "aurora" {
  name        = "${var.project_name}-aurora-subnet-group"
  description = "Subnet group para Aurora RAG Lab"
  subnet_ids  = aws_subnet.private[*].id

  tags = { Name = "${var.project_name}-aurora-subnet-group" }
}

resource "aws_rds_cluster" "aurora" {
  cluster_identifier = "${var.project_name}-aurora"
  engine             = "aurora-postgresql"
  engine_mode        = "provisioned"
  engine_version     = var.aurora_engine_version

  database_name   = "compliance_db"
  master_username = "ragadmin"
  master_password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.aurora.name
  vpc_security_group_ids = [aws_security_group.aurora.id]

  storage_encrypted   = true
  deletion_protection = false
  skip_final_snapshot = true

  serverlessv2_scaling_configuration {
    # 0.5 ACU deja que Aurora baje casi a cero cuando el lab esta idle, que es
    # la mayor parte del tiempo entre ensayos.
    min_capacity = 0.5
    max_capacity = var.environment_type == "prod" ? 16 : 4
  }

  enabled_cloudwatch_logs_exports = ["postgresql"]

  tags = { Name = "${var.project_name}-aurora" }
}

resource "aws_rds_cluster_instance" "aurora" {
  identifier         = "${var.project_name}-aurora-instance"
  cluster_identifier = aws_rds_cluster.aurora.id
  instance_class     = "db.serverless"
  engine             = aws_rds_cluster.aurora.engine
  engine_version     = aws_rds_cluster.aurora.engine_version

  publicly_accessible = false

  tags = { Name = "${var.project_name}-aurora-instance" }
}

# Sin name fijo: Secrets Manager retiene los secrets borrados y un redeploy
# rapido tras el cleanup chocaria con un nombre "ya existente".
resource "aws_secretsmanager_secret" "aurora" {
  name_prefix             = "${var.project_name}-aurora-"
  description             = "Credenciales de Aurora PostgreSQL para el lab RAG"
  recovery_window_in_days = 0
}

resource "aws_secretsmanager_secret_version" "aurora" {
  secret_id = aws_secretsmanager_secret.aurora.id

  secret_string = jsonencode({
    username = "ragadmin"
    password = var.db_password
    host     = aws_rds_cluster.aurora.endpoint
    port     = "5432"
    dbname   = "compliance_db"
  })
}

# ── ElastiCache Redis: memoria de sesion ──────────────────────────────────────
resource "aws_elasticache_subnet_group" "redis" {
  name        = "${var.project_name}-redis-subnet-group"
  description = "Subnet group para Redis RAG Lab"
  subnet_ids  = aws_subnet.private[*].id
}

resource "aws_elasticache_cluster" "redis" {
  cluster_id      = "${var.project_name}-redis"
  engine          = "redis"
  node_type       = "cache.t3.micro"
  num_cache_nodes = 1
  port            = 6379

  subnet_group_name  = aws_elasticache_subnet_group.redis.name
  security_group_ids = [aws_security_group.redis.id]

  tags = { Name = "${var.project_name}-redis" }
}

# ── DynamoDB: memoria persistente por usuario ─────────────────────────────────
# PAY_PER_REQUEST: sin trafico no cuesta nada, que es lo que pasa entre ensayos.
resource "aws_dynamodb_table" "user_memory" {
  name         = var.dynamodb_table_name
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  point_in_time_recovery {
    enabled = false
  }

  tags = { Name = var.dynamodb_table_name }
}

# ── S3: corpus de documentos de compliance ────────────────────────────────────
resource "aws_s3_bucket" "documents" {
  bucket = "${var.project_name}-documents-${data.aws_caller_identity.current.account_id}"

  # El corpus se reingesta en cada montaje del lab; nada que conservar.
  force_destroy = true

  tags = { Name = "${var.project_name}-documents" }
}

# El Lab 1 y el bootstrap ya versionaban sus buckets; aqui faltaba.
resource "aws_s3_bucket_versioning" "documents" {
  bucket = aws_s3_bucket.documents.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "documents" {
  bucket = aws_s3_bucket.documents.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "documents" {
  bucket                  = aws_s3_bucket.documents.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
