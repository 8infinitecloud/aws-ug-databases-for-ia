# Configuracion parcial: bucket, tabla de locks y region llegan de
# infra/backend.hcl, que genera infra/bootstrap.
#
#   terraform init -backend-config=../backend.hcl
terraform {
  backend "s3" {
    key = "lab1/terraform.tfstate"
  }
}
