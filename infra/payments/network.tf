# =============================================================================
# Red del core de pagos
# =============================================================================
# La base del core en EC2, y Fargate, en subnets PUBLICAS con IP publica de
# salida. Es una concesion deliberada: la alternativa correcta —Fargate privado— necesita un
# NAT Gateway para bajar la imagen de ECR, y eso cuesta ~$1.08/dia, mas que todo
# el resto de la demo junta.
#
# El riesgo se acota con el security group: la tarea solo acepta trafico desde el
# ALB, nunca desde internet. En produccion: subnets privadas + NAT o VPC
# endpoints para ECR, S3 y CloudWatch Logs.

data "aws_availability_zones" "available" {
  state = "available"

  # Sin este filtro, names[0] puede devolver una Local Zone si la cuenta se dio
  # de alta en alguna. us-east-1 tiene varias, incluida Lima.
  filter {
    name   = "opt-in-status"
    values = ["opt-in-not-required"]
  }
}

data "aws_caller_identity" "current" {}

locals {
  name_prefix = "${var.project_name}-${var.environment}"
}

resource "aws_vpc" "main" {
  cidr_block           = "10.20.0.0/16"
  enable_dns_hostnames = true
  enable_dns_support   = true

  tags = { Name = "${local.name_prefix}-vpc" }
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-igw" }
}

# Dos AZ: requisito del ALB, y del subnet group de DMS del Lab 1.
resource "aws_subnet" "public" {
  count = 2

  vpc_id                  = aws_vpc.main.id
  cidr_block              = "10.20.${count.index + 1}.0/24"
  availability_zone       = data.aws_availability_zones.available.names[count.index]
  map_public_ip_on_launch = true

  tags = { Name = "${local.name_prefix}-public-${count.index + 1}" }
}

resource "aws_subnet" "private" {
  count = 2

  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${count.index + 11}.0/24"
  availability_zone = data.aws_availability_zones.available.names[count.index]

  tags = { Name = "${local.name_prefix}-private-${count.index + 1}" }
}

resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id

  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }

  tags = { Name = "${local.name_prefix}-public-rt" }
}

resource "aws_route_table_association" "public" {
  count = 2

  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# ── Security groups ───────────────────────────────────────────────────────────

# El VPC Link de API Gateway: sus ENIs viven en las subnets privadas y son lo
# unico que habla con el ALB.
resource "aws_security_group" "vpc_link" {
  name        = "${local.name_prefix}-vpclink-sg"
  description = "ENIs del VPC Link de API Gateway"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Hacia el ALB interno"
    from_port   = 80
    to_port     = 80
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-vpclink-sg" }
}

# El ALB es INTERNO: no tiene IP publica. Solo lo alcanza el VPC Link.
resource "aws_security_group" "alb" {
  name        = "${local.name_prefix}-alb-sg"
  description = "ALB interno del backend de adquirencia"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "HTTP solo desde el VPC Link de API Gateway"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.vpc_link.id]
  }

  egress {
    description     = "Hacia las tareas Fargate"
    from_port       = local.backend_port
    to_port         = local.backend_port
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-alb-sg" }
}

# La tarea del backend: solo acepta desde el ALB.
resource "aws_security_group" "backend" {
  name        = "${local.name_prefix}-backend-sg"
  description = "Tareas Fargate del backend"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "Salida a la base, Secrets Manager, IoT Core y ECR"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-backend-sg" }
}

# Regla separada para romper la dependencia circular ALB <-> backend.
resource "aws_security_group_rule" "backend_from_alb" {
  type                     = "ingress"
  description              = "Solo desde el ALB, nunca desde internet"
  from_port                = local.backend_port
  to_port                  = local.backend_port
  protocol                 = "tcp"
  security_group_id        = aws_security_group.backend.id
  source_security_group_id = aws_security_group.alb.id
}

# La base del core: solo desde el backend y desde DMS. Ni un puerto a internet,
# ni siquiera SSH (se entra por Session Manager), aunque la instancia tenga IP
# publica para su trafico de salida.
resource "aws_security_group" "db" {
  name        = "${local.name_prefix}-db-sg"
  description = "PostgreSQL del core, on-premises simulado en EC2"
  vpc_id      = aws_vpc.main.id

  ingress {
    description     = "PostgreSQL desde el backend"
    from_port       = 5432
    to_port         = 5432
    protocol        = "tcp"
    security_groups = [aws_security_group.backend.id]
  }

  # DMS replica desde aqui al data lake del Lab 1. Se permite todo el CIDR de la
  # VPC porque la instancia de DMS la crea el modulo del Lab 1 y su SG no se
  # conoce aqui.
  ingress {
    description = "PostgreSQL desde DMS, dentro de la VPC"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = [aws_vpc.main.cidr_block]
  }

  # DEMO: abierto a internet para conectarse con pgAdmin desde cualquier red.
  # Riesgo aceptado a proposito (ver docs/well-architected.md). Quitar este
  # bloque lo vuelve a cerrar.
  ingress {
    description = "PostgreSQL desde internet (DEMO, pgAdmin)"
    from_port   = 5432
    to_port     = 5432
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  egress {
    description = "Paquetes, Secrets Manager, S3, SSM y CloudWatch"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-db-sg" }
}

# El SG por defecto de la VPC permite todo entre sus miembros. No lo usamos, pero
# existe: dejarlo sin reglas es gratis.
resource "aws_default_security_group" "default" {
  vpc_id = aws_vpc.main.id
  tags   = { Name = "${local.name_prefix}-default-DO-NOT-USE" }
}

# =============================================================================
# Salida a S3 desde las subnets privadas
# =============================================================================
# Sin esto, lo que vive en las subnets privadas no puede hablar con S3 en
# absoluto: no hay NAT Gateway (concesion deliberada, cuesta ~1 USD/dia) y las
# privadas solo tenian la ruta local.
#
# Se descubrio desplegando: la instancia de replicacion de DMS del Lab 1 va en
# estas subnets, y su endpoint destino es S3. El test de conexion fallaba con
# "Failed to connect to database" —un mensaje que no ayuda nada— y el full load
# no habria podido escribir en el data lake. `plan` no puede detectarlo: la
# configuracion es valida, simplemente no hay camino.
#
# Un Gateway Endpoint es la respuesta correcta y no un parche: **no cuesta
# nada** (ni por hora ni por GB), el trafico no sale de la red de AWS, y evita
# el NAT que la arquitectura queria evitar.

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id

  # Sin ruta a internet a proposito. Lo unico que necesitan estas subnets es
  # llegar a S3, y de eso se encarga el endpoint de abajo.

  tags = { Name = "${local.name_prefix}-private-rt" }
}

resource "aws_route_table_association" "private" {
  count = length(aws_subnet.private)

  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.main.id
  service_name      = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type = "Gateway"

  # El endpoint funciona anadiendo una ruta a los prefijos de S3 en estas tablas.
  # Si no se asocia a ninguna, existe y no sirve para nada.
  route_table_ids = [aws_route_table.private.id]

  tags = { Name = "${local.name_prefix}-s3-endpoint" }
}
