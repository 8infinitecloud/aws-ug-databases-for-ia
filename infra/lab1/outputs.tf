# Los outputs conservan los mismos nombres que los del CloudFormation anterior,
# para que los scripts de los labs sigan funcionando sin cambios de contrato.

output "vpc_id" {
  description = "ID de la VPC"
  value       = aws_vpc.main.id
}

output "rds_endpoint" {
  description = "Endpoint de RDS MySQL"
  value       = aws_db_instance.mysql.address
}

output "rds_port" {
  description = "Puerto de RDS MySQL"
  value       = aws_db_instance.mysql.port
}

output "secret_arn" {
  description = "ARN del Secret con credenciales RDS"
  value       = aws_secretsmanager_secret.rds.arn
}

output "s3_bucket_name" {
  description = "Nombre del bucket S3 del Data Lake"
  value       = aws_s3_bucket.datalake.id
}

output "s3_bucket_arn" {
  description = "ARN del bucket S3"
  value       = aws_s3_bucket.datalake.arn
}

output "dms_task_arn" {
  description = "ARN de la tarea DMS"
  value       = aws_dms_replication_task.main.replication_task_arn
}

output "dms_replication_instance_arn" {
  description = "ARN de la instancia de replicacion DMS"
  value       = aws_dms_replication_instance.main.replication_instance_arn
}

output "dms_role_arn" {
  description = "ARN del rol que usa DMS (lo consume el Lab 2)"
  value       = aws_iam_role.dms.arn
}
