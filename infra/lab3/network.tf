# =============================================================================
# VPC y red
# =============================================================================
# Aurora y Redis en subnets privadas; el EC2 que sirve Streamlit en la publica.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${var.project_name}-vpc" }
}

# Dos AZ: es requisito del subnet group de Aurora.
resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.0.${count.index + 1}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${var.project_name}-private-${count.index + 1}" }
}

resource "aws_subnet" "public" {
  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.0.3.0/24"
  availability_zone       = data.aws_availability_zones.available.names[0]
  map_public_ip_on_launch = true

  tags = { Name = "${var.project_name}-public" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${var.project_name}-igw" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${var.project_name}-public-rt" }
}

resource "aws_route_table_association" "public" {
  subnet_id      = aws_subnet.public.id
  route_table_id = aws_route_table.public.id
}

# ── Security groups ───────────────────────────────────────────────────────────
# La app es el unico cliente de Aurora y Redis: ambos aceptan trafico solo desde
# el SG de la app, nunca desde internet.
resource "aws_security_group" "app" {
  name        = "${var.project_name}-app-sg"
  description = "Security group para la aplicacion RAG"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Salida a Bedrock, S3, DynamoDB y paquetes"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-app-sg" }
}

resource "aws_security_group" "aurora" {
  name        = "${var.project_name}-aurora-sg"
  description = "Security group para Aurora PostgreSQL"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL desde la app"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project_name}-aurora-sg" }
}

resource "aws_security_group" "redis" {
  name        = "${var.project_name}-redis-sg"
  description = "Security group para ElastiCache Redis"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "Redis desde la app"
    from_port       = 6379
    to_port         = 6379
    protocol        = "tcp"
    security_groups = [aws_security_group.app.id]
  }

  tags = { Name = "${var.project_name}-redis-sg" }
}

# Streamlit abierto al mundo: es un lab y hay que poder abrirlo desde el
# proyector. En produccion se restringe a una IP.
resource "aws_security_group" "streamlit" {
  name        = "${var.project_name}-streamlit-sg"
  description = "Permite acceso a la UI de Streamlit desde el navegador"
  vpc_id      = aws_vpc.main.id

  ingress {
    description = "Streamlit UI"
    from_port   = 8501
    to_port     = 8501
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = { Name = "${var.project_name}-streamlit-sg" }
}
