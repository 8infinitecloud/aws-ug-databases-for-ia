variable "aws_region" {
  description = "Region AWS del lab"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Debe coincidir con el project_name del Lab 1"
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

variable "glue_database_name" {
  description = "Base de datos en Glue Data Catalog"
  type        = string
  default     = "financial_platform"
}

variable "glue_job_script_s3_key" {
  description = "Ruta del script del Glue Job dentro del bucket del data lake"
  type        = string
  default     = "scripts/glue_job.py"
}

variable "state_bucket" {
  description = "Bucket del state remoto. Lo rellena scripts/setup.sh desde ../backend.hcl."
  type        = string
}
