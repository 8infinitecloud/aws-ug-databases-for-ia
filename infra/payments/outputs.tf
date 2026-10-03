# Los outputs que consumen los scripts, el firmware del POS y el modulo del Lab 1.

output "api_endpoint" {
  description = "URL base de la API. Es lo que configura el POS y la web de pago."
  value       = aws_apigatewayv2_api.main.api_endpoint
}

output "db_host" {
  description = "IP privada del PostgreSQL del core (EC2). DMS replica desde aqui."
  value       = local.db_private_ip
}

output "db_secret_arn" {
  description = "ARN del secreto con las credenciales de la base"
  value       = aws_secretsmanager_secret.db.arn
}

output "db_instance_id" {
  description = "Instancia EC2 de la base, para Session Manager"
  value       = aws_instance.db.id
}

output "db_tunnel_command" {
  description = "Tunel a la base desde tu maquina (localhost:15432), sin abrir puertos"
  value       = "aws ssm start-session --target ${aws_instance.db.id} --document-name AWS-StartPortForwardingSession --parameters 'portNumber=5432,localPortNumber=15432'"
}

output "artifacts_bucket" {
  description = "Bucket con los ficheros del esquema que aplica la instancia"
  value       = aws_s3_bucket.artifacts.id
}

output "db_name" {
  description = "Nombre de la base transaccional"
  value       = var.db_name
}

output "vpc_id" {
  description = "VPC del core. El Lab 1 despliega DMS aqui para alcanzar la base."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Subnets privadas. Las usa el subnet group de DMS en el Lab 1."
  value       = aws_subnet.private[*].id
}

output "vpc_cidr" {
  description = "CIDR de la VPC"
  value       = aws_vpc.main.cidr_block
}

output "ecr_repository_url" {
  description = "Repositorio de la imagen del backend"
  value       = aws_ecr_repository.backend.repository_url
}

output "ecs_cluster_name" {
  description = "Cluster ECS"
  value       = aws_ecs_cluster.main.name
}

output "ecs_service_name" {
  description = "Servicio del backend"
  value       = aws_ecs_service.backend.name
}

output "alb_dns_name" {
  description = "DNS del ALB. Util para depurar sin pasar por API Gateway."
  value       = aws_lb.backend.dns_name
}

output "iot_endpoint" {
  description = "Endpoint de datos de AWS IoT Core. Lo configura el firmware del POS."
  value       = data.aws_iot_endpoint.data.endpoint_address
}

output "backend_image_pending" {
  description = "true si aun no hay imagen y el servicio esta en 0 tareas"
  value       = var.backend_image == ""
}

output "backend_image" {
  description = "Imagen del backend desplegada ahora (vacia si aun no hay). La lee scripts/deploy_core.sh para no bajar el servicio a 0 al reaplicar"
  value       = var.backend_image
}
