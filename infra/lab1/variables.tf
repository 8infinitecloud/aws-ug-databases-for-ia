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
  description = "Schema del core que replica DMS"
  type        = string
  default     = "acquiring"
}

variable "dms_instance_class" {
  description = <<-DESC
    Instancia de replicacion. El volumen de una demo es minimo: t3.small cuesta
    la mitad que t3.medium. Con CDC esta encendida todo el tiempo, asi que el
    tamano si se nota en la factura.
  DESC
  type        = string
  default     = "dms.t3.small"
}

variable "create_dms_vpc_role" {
  description = <<-DESC
    Crear los roles de cuenta que DMS exige con nombre fijo:
    `dms-vpc-role` y `dms-cloudwatch-logs-role`.

    DMS exige esos nombres exactos: no mira el rol del modulo, por muchos
    permisos que tenga. Son prerrequisitos de cuenta, no de este lab. Sin el
    primero no se puede crear el subnet group; sin el segundo, la tarea no
    escribe logs y sus fallos son indiagnosticables.

    Ponlo a false si tu cuenta ya lo tiene —pasa en cuentas donde alguien uso DMS
    antes— o el apply fallara con EntityAlreadyExists. Para saberlo:
        aws iam get-role --role-name dms-vpc-role
  DESC
  type        = bool
  default     = true
}
