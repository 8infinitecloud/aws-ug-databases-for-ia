output "glue_job_name" {
  description = "Nombre del Glue Job ETL"
  value       = aws_glue_job.etl.name
}

output "crawler_name" {
  description = "Nombre del Glue Crawler"
  value       = aws_glue_crawler.curated.name
}

output "glue_database_name" {
  description = "Base de datos en el Glue Data Catalog"
  value       = aws_glue_catalog_database.main.name
}

output "ruleset_name" {
  description = "Nombre del Glue DQ Ruleset"
  value       = aws_glue_data_quality_ruleset.main.name
}

output "glue_role_arn" {
  description = "ARN del IAM Role de Glue"
  value       = aws_iam_role.glue.arn
}

output "s3_bucket_name" {
  description = "Bucket del data lake (heredado del Lab 1)"
  value       = local.s3_bucket
}

output "curated_s3_path" {
  description = "Ruta S3 de la zona curated"
  value       = "s3://${local.s3_bucket}/curated/transactions/"
}

output "quality_report_s3_path" {
  description = "Ruta S3 del quality report mas reciente"
  value       = "s3://${local.s3_bucket}/quality-reports/latest/quality_report.json"
}

output "athena_workgroup" {
  description = "Workgroup de Athena para consultar la zona curada"
  value       = aws_athena_workgroup.main.name
}

output "athena_results_path" {
  description = "Donde deja Athena los resultados de cada consulta"
  value       = "s3://${local.s3_bucket}/athena-results/"
}

output "athena_table" {
  description = "Tabla que crea el Crawler y que consulta Athena"
  value       = "${aws_glue_catalog_database.main.name}.transactions"
}
