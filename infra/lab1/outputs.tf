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
  description = "ARN de la tarea DMS. Se arranca a mano: aws dms start-replication-task"
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

output "source_aurora_endpoint" {
  description = "Endpoint de Aurora del que replica. Viene de infra/payments."
  value       = local.aurora_endpoint
}

output "source_object" {
  description = "Objeto replicado: schema.vista"
  value       = "${var.source_schema}.${var.source_object}"
}
