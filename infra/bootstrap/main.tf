# =============================================================================
# Bootstrap del backend remoto de Terraform
# =============================================================================
# Se ejecuta UNA SOLA VEZ, antes que cualquier lab, y con state local: es el
# clasico problema del huevo y la gallina, porque crea justo la infraestructura
# donde vivira el state de todo lo demas.
#
#   cd infra/bootstrap
#   terraform init
#   terraform apply
#
# Al terminar escribe infra/backend.hcl, que los tres labs consumen con
#   terraform init -backend-config=../backend.hcl
# =============================================================================

locals {
  state_bucket = "${var.project_name}-tfstate-${data.aws_caller_identity.current.account_id}"
  lock_table   = "${var.project_name}-tflock"
}

data "aws_caller_identity" "current" {}

# ── Bucket del state ──────────────────────────────────────────────────────────
resource "aws_s3_bucket" "tfstate" {
  bucket = local.state_bucket

  # El state describe toda la infraestructura del demo. Que un terraform
  # destroy del bootstrap se lo lleve por delante seria irreversible.
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name    = local.state_bucket
    Project = var.project_name
    Purpose = "terraform-state"
  }
}

# El versionado es lo que permite recuperar un state corrupto o pisado.
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# El state guarda contrasenas en claro (RDS, Aurora). Nunca publico.
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Tabla de locks ────────────────────────────────────────────────────────────
# Evita que dos apply simultaneos (tu laptop y GitHub Actions) corrompan el
# state. PAY_PER_REQUEST: sin trafico, cuesta 0.
resource "aws_dynamodb_table" "tflock" {
  name         = local.lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name    = local.lock_table
    Project = var.project_name
    Purpose = "terraform-state-lock"
  }
}

# ── Configuracion parcial del backend para los labs ───────────────────────────
# El bloque backend de Terraform no admite interpolacion, asi que el nombre del
# bucket (que lleva el account id) no puede vivir dentro de backend.tf. Se
# genera aqui y cada lab lo consume con -backend-config.
resource "local_file" "backend_config" {
  filename = "${path.module}/../backend.hcl"
  content  = <<-EOT
    # Generado por infra/bootstrap. No editar a mano.
    bucket         = "${aws_s3_bucket.tfstate.id}"
    dynamodb_table = "${aws_dynamodb_table.tflock.name}"
    region         = "${var.aws_region}"
    encrypt        = true
  EOT
}
