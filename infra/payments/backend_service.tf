# =============================================================================
# Backend de adquirencia en Fargate
# =============================================================================
# El flujo completo: el terminal y la web de pago llaman a API Gateway, que enruta
# al ALB, que reparte a las tareas Fargate.
#
#   POS / web QR  ──▶  API Gateway (HTTP API)  ──▶  VPC Link  ──▶  ALB  ──▶  Fargate
#
# Por que API Gateway delante y no el ALB directo: da throttling, logs de acceso y
# un punto unico donde anadir autenticacion despues, sin tocar el servicio.

resource "aws_ecr_repository" "backend" {
  name                 = "${local.name_prefix}-backend"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  # El lab se destruye entero: sin esto, destroy falla porque el repositorio
  # tiene imagenes.
  force_delete = true

  tags = { Name = "${local.name_prefix}-backend" }
}

# Solo las ultimas imagenes: sin esto el repositorio crece sin limite.
resource "aws_ecr_lifecycle_policy" "backend" {
  repository = aws_ecr_repository.backend.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Conservar solo las 5 imagenes mas recientes"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 5
      }
      action = { type = "expire" }
    }]
  })
}

resource "aws_cloudwatch_log_group" "backend" {
  name              = "/ecs/${local.name_prefix}-backend"
  retention_in_days = 7 # en un lab no hace falta mas, y CloudWatch cobra por GB

  tags = { Name = "${local.name_prefix}-backend-logs" }
}

# ── IAM ───────────────────────────────────────────────────────────────────────
# Dos roles, y la distincion importa:
#   execution_role  lo usa ECS para arrancar la tarea (bajar la imagen, escribir logs)
#   task_role       lo usa el CODIGO de la aplicacion (leer el secreto, publicar en IoT)
resource "aws_iam_role" "execution" {
  name = "${local.name_prefix}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "execution" {
  role       = aws_iam_role.execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

# El secreto se inyecta como variable de entorno por ECS, asi que el rol de
# ejecucion tambien necesita leerlo.
resource "aws_iam_role_policy" "execution_secrets" {
  name = "ReadDbSecret"
  role = aws_iam_role.execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["secretsmanager:GetSecretValue"]
      Resource = aws_secretsmanager_secret.db.arn
    }]
  })
}

resource "aws_iam_role" "task" {
  name = "${local.name_prefix}-ecs-task"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "task" {
  name = "BackendRuntime"
  role = aws_iam_role.task.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadDbSecret"
        Effect   = "Allow"
        Action   = ["secretsmanager:GetSecretValue"]
        Resource = aws_secretsmanager_secret.db.arn
      },
      {
        # Publicar el evento de pago. Acotado al topic del prefijo de la demo, no
        # a iot:* sobre todo.
        Sid    = "PublishPaymentEvents"
        Effect = "Allow"
        Action = ["iot:Publish"]
        Resource = [
          "arn:aws:iot:${var.aws_region}:${data.aws_caller_identity.current.account_id}:topic/*/events"
        ]
      },
      {
        # Para resolver el endpoint de IoT Core en arranque.
        Sid      = "DescribeIotEndpoint"
        Effect   = "Allow"
        Action   = ["iot:DescribeEndpoint"]
        Resource = "*"
      },
    ]
  })
}

# ── Definicion de la tarea ────────────────────────────────────────────────────
data "aws_iot_endpoint" "data" {
  endpoint_type = "iot:Data-ATS"
}

locals {
  # Si no hay imagen, se usa un placeholder y el servicio arranca con 0 tareas.
  # Permite aplicar la infraestructura antes de construir el backend.
  # El puerto lo fija el Dockerfile (`--port 8080`). Vive aqui una sola vez
  # porque lo usan el contenedor, su health check, el target group y el
  # listener: cuatro sitios que, separados, divergen. Ya paso: estaban en 8000
  # mientras el contenedor escuchaba en 8080, y el ALB mandaba trafico a un
  # puerto donde no habia nadie. `plan` no puede detectarlo.
  backend_port = 8080

  backend_image   = var.backend_image != "" ? var.backend_image : "${aws_ecr_repository.backend.repository_url}:latest"
  backend_desired = var.backend_image != "" ? 1 : 0
}

resource "aws_ecs_cluster" "main" {
  name = "${local.name_prefix}-cluster"

  setting {
    name  = "containerInsights"
    value = "disabled" # cuesta por metrica; en un lab no aporta
  }

  tags = { Name = "${local.name_prefix}-cluster" }
}

resource "aws_ecs_task_definition" "backend" {
  family                   = "${local.name_prefix}-backend"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.backend_cpu
  memory                   = var.backend_memory
  execution_role_arn       = aws_iam_role.execution.arn
  task_role_arn            = aws_iam_role.task.arn

  container_definitions = jsonencode([{
    name      = "backend"
    image     = local.backend_image
    essential = true

    portMappings = [{
      containerPort = local.backend_port
      protocol      = "tcp"
    }]

    environment = [
      { name = "DB_HOST", value = local.db_private_ip },
      { name = "DB_PORT", value = "5432" },
      { name = "DB_NAME", value = var.db_name },
      { name = "DB_USER", value = var.db_username },
      { name = "DB_SECRET_ARN", value = aws_secretsmanager_secret.db.arn },
      { name = "AWS_REGION", value = var.aws_region },
      { name = "IOT_ENDPOINT", value = data.aws_iot_endpoint.data.endpoint_address },
      { name = "DEMO_CVV", value = var.demo_cvv },
      # La URL con la que el comprador llega a la API: la redireccion de 3DS la
      # necesita, porque detras del ALB el host de la peticion no es el publico.
      { name = "PUBLIC_BASE_URL", value = aws_apigatewayv2_api.main.api_endpoint },
    ]

    logConfiguration = {
      logDriver = "awslogs"
      options = {
        "awslogs-group"         = aws_cloudwatch_log_group.backend.name
        "awslogs-region"        = var.aws_region
        "awslogs-stream-prefix" = "backend"
      }
    }

    healthCheck = {
      command     = ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:${local.backend_port}/health')\" || exit 1"]
      interval    = 30
      timeout     = 5
      retries     = 3
      startPeriod = 30
    }
  }])

  tags = { Name = "${local.name_prefix}-backend" }
}

# ── ALB ───────────────────────────────────────────────────────────────────────
resource "aws_lb" "backend" {
  name = "${local.name_prefix}-alb"
  # Interno a proposito: la unica entrada es API Gateway. Un ALB publico se
  # podria llamar directamente, saltandose el throttling y los logs.
  internal           = true
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.private[*].id

  # El lab se destruye: sin esto hay que desactivarlo a mano antes.
  enable_deletion_protection = false

  tags = { Name = "${local.name_prefix}-alb" }
}

resource "aws_lb_target_group" "backend" {
  # name_prefix y no name: cambiar el puerto obliga a recrear el target group, y
  # con nombre fijo el nuevo colisiona con el viejo antes de que este se borre.
  # Con prefijo, AWS le pone un sufijo unico y el reemplazo funciona. El limite
  # del prefijo son 6 caracteres.
  name_prefix = "fcpay"
  port        = local.backend_port
  protocol    = "HTTP"
  vpc_id      = aws_vpc.main.id
  target_type = "ip" # obligatorio con awsvpc en Fargate

  health_check {
    path                = "/health"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
    matcher             = "200"
  }

  # Sin esto, destroy falla mientras el listener lo referencia.
  lifecycle { create_before_destroy = true }

  tags = { Name = "${local.name_prefix}-tg" }
}

resource "aws_lb_listener" "backend" {
  load_balancer_arn = aws_lb.backend.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.backend.arn
  }
}

# ── Servicio ──────────────────────────────────────────────────────────────────
resource "aws_ecs_service" "backend" {
  name            = "${local.name_prefix}-backend"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.backend.arn
  desired_count   = local.backend_desired
  launch_type     = "FARGATE"

  network_configuration {
    subnets = aws_subnet.public[*].id
    # Necesaria para bajar la imagen de ECR sin NAT Gateway. El SG es lo que
    # impide que llegue trafico de internet: solo acepta desde el ALB.
    assign_public_ip = true
    security_groups  = [aws_security_group.backend.id]
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.backend.arn
    container_name   = "backend"
    container_port   = local.backend_port
  }

  # Da tiempo a que la tarea arranque antes de que el ALB la marque insana.
  health_check_grace_period_seconds = 60

  # Sin ignore_changes, y es deliberado. Antes ignoraba `task_definition` y
  # `desired_count` "porque la imagen la despliega un workflow aparte" — pero ese
  # workflow no existe: Terraform es el unico que despliega aqui. El efecto real
  # era que `backend_image` no hacia nada: se aplicaba, el servicio seguia en 0
  # tareas con la task definition vieja, y el ALB apuntaba a un contenedor que ya
  # no correspondia. Si algun dia un CI externo actualiza la task definition,
  # vuelve a ponerse, pero no antes.
  lifecycle {
    ignore_changes = []
  }

  depends_on = [aws_lb_listener.backend]

  tags = { Name = "${local.name_prefix}-backend" }
}
