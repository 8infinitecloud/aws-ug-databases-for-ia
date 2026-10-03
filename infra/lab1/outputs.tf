# Los outputs que consumen los scripts y el modulo del Lab 2.
# Ya no hay rds_endpoint ni secret_arn: el sistema origen es infra/payments, y sus
# datos se leen de alli.

output "s3_bucket_name" {
  description = "Bucket del data lake"
  value       = aws_s3_bucket.datalake.id
}

output "s3_bucket_arn" {
  description = "ARN del bucket del data lake"
  value       = aws_s3_bucket.datalake.arn
}

output "dms_task_arn" {
  description = "ARN de la tarea DMS (full load + CDC). La arranca labs/01/scripts/setup.sh"
  value       = aws_dms_replication_task.main.replication_task_arn
}

output "dms_replication_instance_arn" {
  description = "ARN de la instancia de replicacion"
  value       = aws_dms_replication_instance.main.replication_instance_arn
}

output "dms_role_arn" {
  description = "ARN del rol que usa DMS"
  value       = aws_iam_role.dms.arn
}

output "source_db_host" {
  description = "IP privada del PostgreSQL del core del que replica. Viene de infra/payments."
  value       = local.db_host
}

output "replicated_tables" {
  description = "Tablas que DMS replica a raw/acquiring/<tabla>/"
  value       = local.replicated_tables
}

output "raw_acquiring_path" {
  description = "Donde caen la foto inicial y los deltas del CDC"
  value       = "s3://${aws_s3_bucket.datalake.id}/raw/acquiring/"
}
