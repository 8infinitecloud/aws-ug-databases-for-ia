output "state_bucket" {
  description = "Bucket S3 donde vive el state de los labs"
  value       = aws_s3_bucket.tfstate.id
}

output "lock_table" {
  description = "Tabla DynamoDB de locks"
  value       = aws_dynamodb_table.tflock.name
}

output "backend_config_path" {
  description = "Fichero de configuracion parcial que consumen los labs"
  value       = local_file.backend_config.filename
}
