# =============================================================================
# Lo que el Lab 1 toma del core
# =============================================================================
# Antes este modulo creaba su propia VPC y su propio RDS MySQL. Los dos eran
# duplicados: el sistema transaccional es infra/payments, y la VPC tambien.
#
# Ahora el Lab 1 hace SOLO lo que le corresponde: el data lake y la replicacion
# desde el core hacia el. Eso es lo que significa que payments sea el core.

data "terraform_remote_state" "payments" {
  backend = "s3"

  config = {
    bucket = var.state_bucket
    key    = "payments/terraform.tfstate"
    region = var.aws_region
  }
}

locals {
  # La VPC del core: DMS tiene que vivir aqui para alcanzar la base.
  vpc_id             = data.terraform_remote_state.payments.outputs.vpc_id
  vpc_cidr           = data.terraform_remote_state.payments.outputs.vpc_cidr
  private_subnet_ids = data.terraform_remote_state.payments.outputs.private_subnet_ids

  # El origen de la replicacion: el PostgreSQL del core en EC2.
  db_host   = data.terraform_remote_state.payments.outputs.db_host
  db_secret = data.terraform_remote_state.payments.outputs.db_secret_arn
  db_name   = data.terraform_remote_state.payments.outputs.db_name
}

# Las credenciales de la base, leidas del secreto que creo payments. Asi no hay
# que pasar la contrasena a este modulo.
data "aws_secretsmanager_secret_version" "db" {
  secret_id = local.db_secret
}

locals {
  db_creds = jsondecode(data.aws_secretsmanager_secret_version.db.secret_string)
}
