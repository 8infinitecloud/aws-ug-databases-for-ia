output "aurora_endpoint" {
  description = "Endpoint de Aurora PostgreSQL"
  value       = aws_rds_cluster.aurora.endpoint
}

output "aurora_secret_arn" {
  description = "ARN del Secret con credenciales de Aurora"
  value       = aws_secretsmanager_secret.aurora.arn
}

output "redis_endpoint" {
  description = "Endpoint de ElastiCache Redis"
  value       = aws_elasticache_cluster.redis.cache_nodes[0].address
}

output "dynamodb_table_name" {
  description = "Tabla de memoria de largo plazo"
  value       = aws_dynamodb_table.user_memory.name
}

output "s3_bucket_name" {
  description = "Bucket con el corpus de documentos"
  value       = aws_s3_bucket.documents.id
}

output "app_server_instance_id" {
  description = "Instance ID del App Server (lo usa deploy-app.sh via SSM)"
  value       = aws_instance.app.id
}

output "app_server_public_ip" {
  description = "IP publica del App Server"
  value       = aws_instance.app.public_ip
}

output "streamlit_url" {
  description = "URL de la UI de Streamlit"
  value       = "http://${aws_instance.app.public_ip}:8501"
}
