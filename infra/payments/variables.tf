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
  description = "Contrasena del usuario de la base (export TF_VAR_db_password=...)"
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
  description = "Usuario de la base. Tambien lo usa DMS para replicar."
  type        = string
  default     = "fincorpadmin"
}

variable "db_instance_type" {
  description = "Instancia del PostgreSQL del core. ARM (Graviton): mas barata por lo mismo."
  type        = string
  default     = "t4g.small"
}

variable "db_volume_gb" {
  description = "Disco del PostgreSQL. Es donde se acumula el WAL si DMS se para."
  type        = number
  default     = 20
}

variable "db_max_slot_wal_keep_size" {
  description = <<-DESC
    Tope de WAL que un slot de replicacion puede retener. Pasado, PostgreSQL
    invalida el slot (DMS tendria que recargar) en vez de llenar el disco y
    tumbar el core. Muy por debajo de db_volume_gb a proposito.
  DESC
  type        = string
  default     = "4GB"
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
