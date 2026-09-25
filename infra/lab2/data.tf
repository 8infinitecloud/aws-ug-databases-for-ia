# =============================================================================
# Datos del Lab 1
# =============================================================================
# En CloudFormation habia que pasar el nombre del bucket a mano como parametro,
# copiandolo de los outputs del otro stack. Terraform lee el state del Lab 1
# directamente: si el Lab 1 cambia de bucket, el Lab 2 se entera solo.

data "terraform_remote_state" "lab1" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "lab1/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  s3_bucket     = data.terraform_remote_state.lab1.outputs.s3_bucket_name
  s3_bucket_arn = data.terraform_remote_state.lab1.outputs.s3_bucket_arn
}
