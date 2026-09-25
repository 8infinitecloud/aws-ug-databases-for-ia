# =============================================================================
# S3 Data Lake - zonas raw/ y curated/
# =============================================================================
# Un solo bucket con prefijos, no un bucket por zona. La separacion que importa
# para ensenar es la conceptual, y con prefijos se ve toda junta en una pantalla.

data "aws_caller_identity" "current" {}

resource "aws_s3_bucket" "datalake" {
  bucket = "${var.project_name}-${data.aws_caller_identity.current.account_id}"

  # El cleanup del lab vacia y borra el bucket a proposito (ver scripts/
  # cleanup.sh). force_destroy permite que terraform destroy haga lo mismo sin
  # dejar el bucket huerfano con objetos versionados dentro.
  force_destroy = true

  tags = {
    Name = "${var.project_name}-datalake"
  }
}

resource "aws_s3_bucket_versioning" "datalake" {
  bucket = aws_s3_bucket.datalake.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "datalake" {
  bucket                  = aws_s3_bucket.datalake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  rule {
    id     = "MoveOldRawToIA"
    status = "Enabled"

    filter {
      prefix = "raw/"
    }

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }
  }

  rule {
    id     = "MoveOldCuratedToIA"
    status = "Enabled"

    filter {
      prefix = "curated/"
    }

    transition {
      days          = 180
      storage_class = "STANDARD_IA"
    }
  }
}

# DMS escribe en raw/ asumiendo el rol de abajo; la policy del bucket se lo
# permite explicitamente.
resource "aws_s3_bucket_policy" "datalake" {
  bucket = aws_s3_bucket.datalake.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "AllowDMSWrite"
      Effect = "Allow"
      Principal = {
        AWS = aws_iam_role.dms.arn
      }
      Action = [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:GetObject",
        "s3:ListBucket",
      ]
      Resource = [
        aws_s3_bucket.datalake.arn,
        "${aws_s3_bucket.datalake.arn}/*",
      ]
    }]
  })

  # La policy referencia el rol; sin esto Terraform puede intentar crearla antes.
  depends_on = [aws_iam_role.dms]
}
