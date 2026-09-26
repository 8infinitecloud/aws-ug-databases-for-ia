# =============================================================================
# VPC y red
# =============================================================================
# RDS y DMS viven en subnets privadas. La subnet publica existe solo para un
# bastion temporal si hiciera falta depurar durante el lab.

data "aws_availability_zones" "available" {
  state = "available"

  # Sin este filtro, names[0] puede devolver una Local Zone si la cuenta se ha
  # dado de alta en alguna. us-east-1 tiene varias, incluida Lima
  # (us-east-1-lim-1a), y RDS/Aurora/ElastiCache no se despliegan ahi igual.
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.project_name}-${var.environment}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-${var.environment}-igw" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.1.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-${var.environment}-public-1" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project_name}-${var.environment}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# Dos subnets privadas en AZ distintas: RDS exige al menos dos para el subnet
# group, aunque la instancia del lab sea single-AZ.
resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 2}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-${var.environment}-private-${count.index + 1}" }
}

# ── Security groups ───────────────────────────────────────────────────────────
resource "aws_security_group" "dms" {
  name        = "${var.project_name}-${var.environment}-dms-sg"
  description = "DMS Replication Instance - salida a RDS y S3"
  vpc_id      = aws_vpc.main.id

  lifecycle {
    create_before_destroy = true
  }

  egress {
    description = "MySQL RDS dentro de la VPC"
    from_port   = 3306
    to_port     = 3306
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  egress {
    description = "S3 endpoint (HTTPS)"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-${var.environment}-dms-sg" }
}

resource "aws_security_group" "rds" {
  name        = "${var.project_name}-${var.environment}-rds-sg"
  description = "RDS MySQL - solo acepta conexiones desde DMS"
  vpc_id      = aws_vpc.main.id

  lifecycle {
    create_before_destroy = true
  }

  ingress {
    description     = "DMS Replication Instance"
    from_port       = 3306
    to_port         = 3306
    protocol        = "tcp"
    security_groups = [aws_security_group.dms.id]
  }

  tags = { Name = "${var.project_name}-${var.environment}-rds-sg" }
}

# La VPC trae un security group por defecto que permite todo el trafico entre
# sus miembros. No lo usamos, pero existe: dejarlo sin reglas es gratis y evita
# que algo acabe ahi por descuido.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id

  tags = { Name = "${var.project_name}-${var.environment}-default-DO-NOT-USE" }
}
