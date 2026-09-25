variable "aws_region" {
  description = "Region donde vive el backend de Terraform"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de nombres de recursos"
  type        = string
  default     = "fincorp-datalake"

  validation {
    condition     = can(regex("^[a-z0-9-]+$", var.project_name))
    error_message = "Solo minusculas, digitos y guiones (va en el nombre de un bucket S3)."
  }
}
