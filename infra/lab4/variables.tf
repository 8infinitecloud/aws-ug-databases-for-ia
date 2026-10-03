variable "aws_region" {
  description = "Region de AWS"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Prefijo de los recursos"
  type        = string
  default     = "fincorp-datalake"
}

variable "environment" {
  description = "Entorno"
  type        = string
  default     = "lab"
}

variable "state_bucket" {
  description = "Bucket con el state de Terraform (lo crea infra/bootstrap)"
  type        = string
}

# ── Umbrales del scoring ─────────────────────────────────────────────────────
# Van como variables y llegan a la Lambda por entorno: se pueden ajustar desde
# la consola antes de la charla sin redesplegar nada ni reflashear el POS.

variable "window_seconds" {
  description = "Ventana de los contadores de velocidad, en segundos"
  type        = number
  default     = 60
}

variable "max_tx_per_window" {
  description = "Operaciones por terminal y ventana antes de marcar"
  type        = number
  default     = 5
}

variable "max_amount_per_window" {
  description = "Monto acumulado por ventana antes de marcar"
  type        = number
  default     = 500
}

variable "max_tx_per_window_online" {
  description = "Operaciones por tienda online y ventana antes de marcar (suma a todos sus compradores)"
  type        = number
  default     = 30
}

variable "max_amount_per_window_online" {
  description = "Monto acumulado por tienda online y ventana antes de marcar"
  type        = number
  default     = 20000
}

variable "max_decline_ratio" {
  description = "Proporcion de declinaciones que enciende la alarma"
  type        = number
  default     = 0.5
}

variable "high_risk_amount" {
  description = "Importe que en un comercio de riesgo alto pide revision"
  type        = number
  default     = 300
}

variable "firehose_buffer_seconds" {
  description = "Buffer de Firehose. 60 es el minimo que admite el servicio."
  type        = number
  default     = 60

  validation {
    condition     = var.firehose_buffer_seconds >= 60
    error_message = "Firehose no admite menos de 60 segundos de buffer."
  }
}
