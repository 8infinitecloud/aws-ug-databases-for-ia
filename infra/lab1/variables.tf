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

variable "state_bucket" {
  description = <<-DESC
    Bucket del state remoto. Lo rellena scripts/setup.sh desde ../backend.hcl.
    Hace falta para leer el state de infra/payments, que es el sistema origen.
  DESC
  type        = string
}

variable "source_schema" {
  description = "Schema de Aurora del que replica DMS"
  type        = string
  default     = "acquiring"
}

variable "source_object" {
  description = <<-DESC
    Objeto que DMS replica. Por defecto la vista v_transactions_for_ingestion, que
    entrega el dato ya aplanado y aisla el schema interno del consumidor.
  DESC
  type        = string
  default     = "v_transactions_for_ingestion"
}

variable "create_dms_vpc_role" {
  description = <<-DESC
    Crear el rol `dms-vpc-role` de la cuenta.

    DMS exige un rol con ESE nombre exacto para crear subnet groups: no mira el
    rol del modulo, por muchos permisos que tenga. Es un prerrequisito de cuenta,
    no de este lab.

    Ponlo a false si tu cuenta ya lo tiene —pasa en cuentas donde alguien uso DMS
    antes— o el apply fallara con EntityAlreadyExists. Para saberlo:
        aws iam get-role --role-name dms-vpc-role
  DESC
  type        = bool
  default     = true
}
