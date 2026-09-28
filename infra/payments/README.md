# infra/payments — el core de FinCorp Payments

La infraestructura del adquirente. Es el **sistema origen** de toda la plataforma
de datos: aquí nace el dato cuando alguien paga.

```
POS / web QR  ──▶  API Gateway  ──▶  VPC Link  ──▶  ALB interno  ──▶  Fargate
                                                                        │
                                                          Aurora PostgreSQL
                                                             Serverless v2
                                                                        │
                                                          (Lab 1) DMS ──▶ S3
```

---

## Qué crea

41 recursos. Los que importan:

| Recurso | Por qué |
|---|---|
| **Aurora PostgreSQL Serverless v2** | La base transaccional. Serverless porque entre ensayos la demo está inactiva y baja a su capacidad mínima sola |
| **Cluster parameter group con `rds.logical_replication = 1`** | Es lo que permite que DMS lea los cambios. El equivalente al binlog de MySQL |
| **ECS Fargate + ALB interno** | El backend de adquirencia |
| **API Gateway HTTP API + VPC Link** | La única puerta de entrada |
| **ECR** | La imagen del backend |
| **Secrets Manager** | La contraseña de Aurora, nunca en el código |

---

## Tres decisiones que conviene entender

### El ALB es interno

Con un ALB público, cualquiera podría llamarlo directamente y **saltarse el
throttling y los logs de API Gateway**. Interno + VPC Link hace que API Gateway
sea la única entrada.

### Fargate va en subnets públicas

Es una concesión deliberada. La alternativa correcta —Fargate en subnets
privadas— necesita un **NAT Gateway** para bajar la imagen de ECR, y eso cuesta
**~$1.08/día**: más que todo el resto de la demo junta.

El riesgo se acota con el security group: la tarea **solo acepta tráfico desde el
ALB**, nunca desde internet. En producción: subnets privadas con NAT, o VPC
endpoints para ECR, S3 y CloudWatch Logs.

### Se puede aplicar sin tener la imagen

`backend_image` vacío (el valor por defecto) crea el servicio ECS con **0 tareas**.
Permite levantar Aurora y la red antes de que el backend exista, que es el orden
natural de construcción.

```bash
# Primero la infraestructura
terraform apply

# Después, con la imagen construida y subida a ECR
terraform apply -var backend_image="<ecr_url>:v1"
```

---

## Desplegar

```bash
export TF_VAR_db_password='CambiaEstaPassword2026!'

terraform init -backend-config=../backend.hcl
terraform apply
```

**~10 minutos**, casi todo Aurora.

Después, aplicar el schema. Aurora está en subnets privadas, así que no es
alcanzable desde tu portátil: hay que hacerlo desde dentro de la VPC o por un
túnel SSM.

```bash
# Las tres migraciones, en orden
for f in ../../payments/schema/00*.sql; do psql "$CONN" -f "$f"; done
```

---

## Coste

| Recurso | Coste |
|---|---|
| Aurora Serverless v2, 0.5 ACU mínimo | ~$0.06/hora |
| Fargate, 0.5 vCPU + 1 GB | ~$0.025/hora |
| **ALB** | **~$0.0225/hora** — el segundo más caro, y no escala a cero |
| API Gateway HTTP API | $1.00 por millón de peticiones (~$0 en la demo) |
| ECR, Secrets Manager, CloudWatch | < $0.01 |
| **Total** | **~$0.11/hora · ~$2.60/día si se olvida el cleanup** |

> Aurora y el ALB **no bajan a cero**. Si se deja encendido, corre.

---

## Destruir

```bash
terraform destroy
```

El bucket de ECR lleva `force_delete` y el ALB no tiene protección de borrado, así
que el `destroy` no se queda a medias.

> Si el Lab 1 desplegó DMS en esta VPC, **destrúyelo primero**: sus ENIs
> bloquearían el borrado de las subnets.

---

## Salidas que consumen otros módulos

| Output | Quién lo usa |
|---|---|
| `api_endpoint` | El firmware del POS y la web de pago QR |
| `iot_endpoint` | El firmware del POS |
| `aurora_endpoint`, `aurora_secret_arn` | El Lab 1, para el endpoint de DMS |
| `vpc_id`, `private_subnet_ids`, `vpc_cidr` | El Lab 1, para desplegar DMS en esta VPC |
| `ecr_repository_url`, `ecs_cluster_name`, `ecs_service_name` | El workflow de despliegue del backend |
