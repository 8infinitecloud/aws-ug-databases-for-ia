# infra/payments — el core de FinCorp Payments

La infraestructura del adquirente. Es el **sistema origen** de toda la plataforma
de datos: aquí nace el dato cuando alguien paga.

```
POS / web QR / tienda online ──▶ API Gateway ──▶ VPC Link ──▶ ALB interno ──▶ Fargate
                                                                               │
                                                        PostgreSQL 16 sobre EC2
                                                        ("on-premises" simulado)
                                                                               │ WAL
                                                              (Lab 1) DMS CDC ──▶ S3
```

---

## Qué crea

| Recurso | Por qué |
|---|---|
| **EC2 `t4g.small` con PostgreSQL 16** | El core "on-premises": lo que tiene la mayoría de bancos y adquirentes, y lo que justifica DMS con CDC en el Lab 1 |
| **`wal_level = logical` + `postgresql16-contrib`** | El CDC lee el WAL decodificado con `test_decoding`, que viene en contrib |
| **`max_slot_wal_keep_size`** y dos **alarmas** | Que un slot de DMS parado no llene el disco y tumbe el core |
| **Bucket de artefactos** | Los `.sql` del esquema: la instancia los aplica sola al arrancar |
| **ECS Fargate + ALB interno** | El backend de adquirencia |
| **API Gateway HTTP API + VPC Link** | La única puerta de entrada |
| **ECR** | La imagen del backend |
| **Secrets Manager** | La contraseña de la base, nunca en el código |

---

## Tres decisiones que conviene entender

### El ALB es interno

Con un ALB público, cualquiera podría llamarlo directamente y **saltarse el
throttling y los logs de API Gateway**. Interno + VPC Link hace que API Gateway
sea la única entrada.

### Fargate y la base van en subnets públicas, sin un puerto abierto

Es una concesión deliberada. La alternativa correcta —subnets privadas—
necesita un **NAT Gateway** para bajar la imagen de ECR, instalar paquetes y
hablar con SSM, y eso cuesta **~$1.08/día**: más que todo el resto de la demo
junta.

El riesgo se acota con los security groups: la tarea **solo acepta tráfico desde
el ALB**, y la base **solo el 5432 desde el backend y desde la VPC** (DMS). Ni
siquiera SSH: a la instancia se entra por **Session Manager**. La IP pública es
solo de salida. En producción: subnets privadas con NAT, o VPC endpoints.

### Un slot de replicación puede tumbar la base

Para el CDC, DMS crea un *replication slot*, y PostgreSQL **retiene el WAL**
hasta que DMS lo consume. Si la tarea se para y nadie borra el slot, el WAL
crece hasta llenar el disco y **la base de producción cae**. Es el fallo clásico
del CDC sobre PostgreSQL.

Tres defensas:

1. `max_slot_wal_keep_size = 4GB` (disco de 20 GB): pasado el tope, PostgreSQL
   invalida el slot en vez de llenar el disco. Mejor perder el CDC que el core.
2. La instancia publica cada minuto `FinCorp/Postgres RetainedWalBytes` y
   `DiskUsedPercent`, con alarmas a 1 GiB y al 80 %.
3. El `cleanup` del Lab 1 borra el slot al destruir la tarea.

### Se puede aplicar sin tener la imagen

`backend_image` vacío (el valor por defecto) crea el servicio ECS con **0 tareas**.
Permite levantar la base y la red antes de que el backend exista, que es el
orden natural de construcción.

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

**~5 minutos.** La instancia arranca, instala PostgreSQL, aplica
`payments/schema/0*.sql` (esquema, seed y cartera de comercios) y queda lista
sin pasos manuales. Su log: `/var/log/fincorp-db-init.log`, por Session Manager.

Si cambias un `.sql` después:

```bash
terraform apply                 # sube los ficheros nuevos al bucket
../../scripts/apply_schema.sh   # la instancia los reaplica (SSM Run Command)
```

Para conectarte con pgAdmin, en la demo el 5432 está **abierto a internet**
(decisión explícita; ver `docs/well-architected.md`):

| Campo | Valor |
|---|---|
| Host | `terraform output -raw db_public_ip` |
| Puerto | `5432` |
| Base | `acquiring` |
| Usuario | `fincorpadmin` |
| Contraseña | `aws secretsmanager get-secret-value --secret-id "$(terraform output -raw db_secret_arn)" --query SecretString --output text \| jq -r .password` |
| SSL | `prefer` (la base no tiene certificado) |

Para cerrarlo, quita el bloque marcado como DEMO en `network.tf` y la línea
`0.0.0.0/0` de `pg_hba.conf`. Sin abrir ningún puerto también se puede, por un
túnel de Session Manager:

```bash
$(terraform output -raw db_tunnel_command)   # deja el túnel abierto
psql "host=localhost port=15432 dbname=acquiring user=fincorpadmin"
```

---

## Coste

| Recurso | Coste |
|---|---|
| EC2 `t4g.small` + 20 GB gp3 | ~$0.019/hora |
| Fargate, 0.5 vCPU + 1 GB | ~$0.025/hora |
| **ALB** | **~$0.0225/hora** — no escala a cero |
| API Gateway HTTP API | $1.00 por millón de peticiones (~$0 en la demo) |
| ECR, Secrets Manager, CloudWatch, S3 | < $0.01 |
| **Total** | **~$0.07/hora · ~$1.60/día si se olvida el cleanup** |

> Antes, con Aurora Serverless v2 (0,5 ACU mínimo, ~$0.06/h), eran ~$0.11/hora.

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
| `db_host`, `db_secret_arn` | El Lab 1, para el endpoint de DMS |
| `db_instance_id`, `db_tunnel_command` | `scripts/apply_schema.sh`, `provision_demo_keys.py` |
| `vpc_id`, `private_subnet_ids`, `vpc_cidr` | El Lab 1, para desplegar DMS en esta VPC |
| `ecr_repository_url`, `ecs_cluster_name`, `ecs_service_name` | El workflow de despliegue del backend |
