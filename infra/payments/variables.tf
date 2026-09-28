variable "aws_region" {
  description = "Region AWS"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de nombres de recursos"
  type        = string
  default     = "fincorp-payments"
}

variable "environment" {
  description = "Entorno"
  type        = string
  default     = "lab"

  validation {
    condition     = contains(["lab", "dev", "prod"], var.environment)
    error_message = "Debe ser lab, dev o prod."
  }
}

variable "db_password" {
  description = "Contrasena maestra de Aurora (export TF_VAR_db_password=...)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "Minimo 8 caracteres."
  }
}

variable "db_name" {
  description = "Base de datos transaccional"
  type        = string
  default     = "acquiring"
}

variable "db_username" {
  description = "Usuario maestro de Aurora"
  type        = string
  default     = "fincorpadmin"
}

variable "aurora_engine_version" {
  description = "Version de Aurora PostgreSQL. Las deprecadas no permiten crear clusters nuevos."
  type        = string
  default     = "16.10"
}

variable "aurora_min_acu" {
  description = "Capacidad minima de Aurora Serverless v2. 0 permite pausar el cluster."
  type        = number
  default     = 0.5
}

variable "aurora_max_acu" {
  description = "Capacidad maxima"
  type        = number
  default     = 4
}

variable "backend_image" {
  description = <<-DESC
    Imagen del backend. Si esta vacio, el servicio ECS se crea con 0 tareas y
    queda esperando: permite aplicar la infraestructura antes de tener imagen.
  DESC
  type        = string
  default     = ""
}

variable "backend_cpu" {
  description = "CPU de la tarea Fargate (256 = 0.25 vCPU)"
  type        = number
  default     = 512
}

variable "backend_memory" {
  description = "Memoria en MiB. Debe ser compatible con backend_cpu."
  type        = number
  default     = 1024
}

variable "demo_cvv" {
  description = <<-DESC
    DEMO: el CVV que el mock aprueba. Va por variable de entorno de la tarea para
    poder cambiarlo desde la consola antes de la charla, sin reflashear el POS ni
    redesplegar la imagen.
  DESC
  type        = string
  default     = "123"
}
