variable "aws_region" {
  description = "Region AWS del lab"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de nombres de recursos del Lab 3"
  type        = string
  default     = "rag-lab"
}

variable "environment_type" {
  description = "Tipo de ambiente: afecta el techo de escalado de Aurora"
  type        = string
  default     = "lab"

  validation {
    condition     = contains(["lab", "dev", "prod"], var.environment_type)
    error_message = "Debe ser lab, dev o prod."
  }
}

variable "db_password" {
  description = "Password de Aurora (export TF_VAR_db_password=...)"
  type        = string
  sensitive   = true

  validation {
    condition     = length(var.db_password) >= 8
    error_message = "Minimo 8 caracteres."
  }
}

variable "aurora_engine_version" {
  description = "Version de Aurora PostgreSQL. Las deprecadas no permiten crear clusters nuevos."
  type        = string
  default     = "16.10"
}

variable "dynamodb_table_name" {
  description = "Tabla de memoria de largo plazo. El codigo la lee por nombre."
  type        = string
  default     = "rag_user_memory"
}

variable "bedrock_model_ids" {
  description = <<-DESC
    Modelos de Bedrock que la app puede invocar. El rol del EC2 se acota a
    estos: si cambias BEDROCK_LLM_MODEL en el .env, anade aqui su id tambien.
  DESC
  type        = list(string)
  default = [
    "amazon.titan-embed-text-v2:0",
    "amazon.nova-pro-v1:0",
  ]
}
