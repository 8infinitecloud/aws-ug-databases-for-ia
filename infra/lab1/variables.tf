variable "aws_region" {
  description = "Region AWS del lab"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de nombres de recursos"
  type        = string
  default     = "fincorp-datalake"
}

variable "environment" {
  description = "Entorno del lab"
  type        = string
  default     = "lab"

  validation {
    condition     = contains(["lab", "dev", "prod"], var.environment)
    error_message = "Debe ser lab, dev o prod."
  }
}

variable "db_username" {
  description = "Usuario administrador de RDS MySQL"
  type        = string
  default     = "fincorpadmin"
}

variable "db_password" {
  description = "Contrasena de RDS MySQL (export TF_VAR_db_password=...)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "Minimo 8 caracteres."
  }
}

variable "db_name" {
  description = "Nombre de la base de datos en RDS"
  type        = string
  default     = "fincorp"
}

variable "mysql_version" {
  description = "Version de RDS MySQL. Debe ser una version concreta, no '8.0'."
  type        = string
  default     = "8.0.43"
}
