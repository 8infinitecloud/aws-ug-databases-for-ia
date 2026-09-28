#   terraform init -backend-config=../backend.hcl
terraform {
  backend "s3" {
    key = "payments/terraform.tfstate"
  }
}
