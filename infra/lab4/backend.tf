#   terraform init -backend-config=../backend.hcl
terraform {
  backend "s3" {
    key = "lab4/terraform.tfstate"
  }
}
