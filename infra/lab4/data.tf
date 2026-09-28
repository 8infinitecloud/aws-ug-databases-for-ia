# =============================================================================
# Lo que el Lab 4 toma de los demas
# =============================================================================
# El bucket del lago lo crea el Lab 1 y el topic de IoT lo alimenta el backend
# de payments. Leyendo el state en vez de pasar nombres a mano, si cualquiera de
# los dos cambia, este lab se entera solo.

data "terraform_remote_state" "lab1" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "lab1/terraform.tfstate"
    region = var.aws_region
  }
}

data "aws_caller_identity" "current" {}
data "aws_region" "current" {}

locals {
  name_prefix   = "${var.project_name}-${var.environment}"
  s3_bucket     = data.terraform_remote_state.lab1.outputs.s3_bucket_name
  s3_bucket_arn = data.terraform_remote_state.lab1.outputs.s3_bucket_arn

  # El POS publica en <serial>/events. El comodin cubre cualquier terminal sin
  # tener que redesplegar la regla al dar de alta uno nuevo.
  iot_topic_filter = "+/events"
}
