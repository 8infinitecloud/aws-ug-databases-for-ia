# Troubleshooting

Errores reales que salen al hacer este workshop, con su causa y su arreglo.
Ordenados por dónde aparecen.

---

## Preparación

### `preflight.sh` dice que Terraform es demasiado antiguo

```
✗ Terraform v1.5.7 es demasiado antigua
```

Los módulos usan `validation` en variables y `templatefile`, que necesitan
≥ 1.6. Actualiza: `brew upgrade terraform` o descarga de
[releases.hashicorp.com](https://releases.hashicorp.com/terraform/).

### `✗ Modelo NO habilitado: amazon.nova-pro-v1`

No has habilitado el modelo en Bedrock, o lo habilitaste en otra región.
Bedrock es **por región**: `us-east-1` y `us-east-2` son listas distintas.
Ver [setup.md §4](setup.md#4-habilitar-los-modelos-de-bedrock).

### `An error occurred (AccessDeniedException) ... bedrock:ListFoundationModels`

Tu usuario no tiene permiso de Bedrock. Solo bloquea el Lab 3; los Labs 1 y 2
funcionan igual.

---

## Backend de Terraform

### `Error: Failed to get existing workspaces: S3 bucket does not exist`

No has corrido el bootstrap, o lo corriste en otra región/cuenta.

```bash
cd infra/bootstrap && terraform init && terraform apply
```

### `Error: Backend initialization required`

Falta el `-backend-config`. Los labs no llevan el nombre del bucket dentro del
código (lleva tu account id), así que hay que pasárselo:

```bash
terraform -chdir=infra/lab1 init -backend-config=../backend.hcl
```

### `Error acquiring the state lock`

Un `apply` anterior se cortó y dejó el lock puesto. Comprueba que no haya otro
proceso corriendo y luego:

```bash
terraform -chdir=infra/lab1 force-unlock <LOCK_ID>
```

El `LOCK_ID` sale en el propio mensaje de error.

---

## Lab 1 — Ingestion + Data Lake

### `terraform apply` tarda muchísimo

Es normal. La instancia de replicación de DMS tarda ~10 de los ~15 minutos
totales. No la interrumpas: un `apply` a medias deja recursos huérfanos que hay
que limpiar a mano.

### `InvalidParameterValue: Cannot find version 8.0.43 for mysql`

AWS retira versiones de motor con el tiempo. Mira las disponibles y ajusta:

```bash
aws rds describe-db-engine-versions --engine mysql \
  --query "DBEngineVersions[].EngineVersion" --output table
```

```bash
terraform -chdir=infra/lab1 apply -var mysql_version=8.0.44
```

### La tarea DMS se queda en `ready` y no arranca

`ready` significa creada pero no iniciada. Es a propósito: el workshop la lanza
cuando tú decides. Ver el Paso 2 del Lab 1.

### La tarea DMS falla con `Test Endpoint failed: Application-Detailed-Failure`

DMS no alcanza a MySQL. Casi siempre es una de estas tres:

1. RDS aún no está `available` — espera y reintenta
2. El security group no permite el tráfico (Terraform lo configura; si lo
   editaste a mano, revísalo)
3. La contraseña no coincide — si cambiaste `TF_VAR_db_password` después de
   crear RDS, el endpoint de DMS quedó con la antigua

### CDC falla tras completar el full load

MySQL necesita backups automáticos activos para retener el binlog. El módulo
pone `backup_retention_period = 1` justo por esto. Si lo bajaste a 0, el full
load funciona pero el CDC no.

### `raw/` sigue vacío después de la tarea DMS

Comprueba el estado real:

```bash
aws dms describe-replication-tasks \
  --query "ReplicationTasks[].[ReplicationTaskIdentifier,Status,ReplicationTaskStats.FullLoadProgressPercent]" \
  --output table
```

Si está en `running` al 100%, mira que estés listando el bucket correcto:

```bash
terraform -chdir=infra/lab1 output -raw s3_bucket_name
```

**Salida de emergencia** (si DMS no cooperara y quieres seguir al Lab 2):

```bash
BUCKET=$(terraform -chdir=infra/lab1 output -raw s3_bucket_name)
aws s3 cp labs/01-ingestion-data-lake/dataset/transactions.csv \
  "s3://$BUCKET/raw/transactions/transactions.csv"
aws s3 cp labs/01-ingestion-data-lake/dataset/customers.csv \
  "s3://$BUCKET/raw/customers/customers.csv"
```

### `mysql: command not found` al cargar el dataset

Falta el cliente. En macOS, `brew install mysql-client` **y** añadirlo al
`PATH` — ver [setup.md §2](setup.md#2-instalar-las-herramientas).

---

## Lab 2 — Transformation + Quality + Catalog

### `El modulo infra/lab1 no tiene recursos`

El Lab 2 lee el estado del Lab 1: necesita que el Lab 1 esté desplegado. No es
un fallo del Lab 2.

### `Error: Unsupported argument: state_bucket`

Estás corriendo `terraform apply` a mano sin pasar la variable. Usa
`scripts/setup.sh`, que la deduce de `backend.hcl`, o pásala:

```bash
export TF_VAR_state_bucket=$(grep bucket infra/backend.hcl | cut -d'"' -f2)
```

### El Glue Job falla con `ModuleNotFoundError: No module named 'pandas'`

Glue Python Shell trae pandas en `GlueVersion 3.0`. Si cambiaste la versión,
vuelve a `3.0` o declara las librerías con `--additional-python-modules`.

### El Glue Job falla con `NoSuchKey: scripts/glue_job.py`

El script no se subió a S3. `scripts/setup.sh` lo hace; si aplicaste Terraform
a mano, súbelo:

```bash
BUCKET=$(terraform -chdir=infra/lab1 output -raw s3_bucket_name)
aws s3 cp labs/02-transformation-quality-catalog/glue_job.py \
  "s3://$BUCKET/scripts/glue_job.py"
```

### El quality report dice que fallan reglas

**Eso es lo esperado.** El dataset trae problemas deliberados para que las
reglas los detecten. Un report con todo en verde significaría que la validación
no está mirando de verdad.

### El Crawler no crea la tabla `transactions_curated`

El Crawler necesita Parquet en `curated/transactions/`. Si el Glue Job no
escribió nada, no hay esquema que descubrir. Mira los logs del job en
CloudWatch (`/aws-glue/jobs/...`) antes de culpar al Crawler.

---

## Lab 3 — Data to AI

### `Error: creating RDS Cluster: InvalidParameterCombination ... engine version`

AWS deprecó esa versión de Aurora PostgreSQL. Mira las válidas:

```bash
aws rds describe-db-engine-versions --engine aurora-postgresql \
  --query "DBEngineVersions[].EngineVersion" --output table
```

```bash
terraform -chdir=infra/lab3 apply -var aurora_engine_version=16.11
```

Necesita ser una versión con soporte de `pgvector` (16.x lo tiene).

### Streamlit no responde en el puerto 8501

Por orden:

```bash
# 1. ¿Está el EC2 arriba?
terraform -chdir=infra/lab3 output -raw app_server_instance_id

# 2. ¿Se desplegó la app?
./labs/03-data-to-ai/infrastructure/deploy-app.sh

# 3. ¿Qué dice el log?
aws ssm start-session --target <instance-id>
sudo tail -50 /var/log/streamlit.log
```

El `UserData` del EC2 solo escribe el `.env`; el código lo sube
`deploy-app.sh`. Si nunca lo corriste, no hay app que responder.

### `could not connect to server: Connection timed out` contra Aurora

Aurora vive en subnets privadas: **no es alcanzable desde tu laptop**, por
diseño. `init_db.py` e `ingest_documents.py` tienen que correr desde el EC2, o
a través de un túnel SSM:

```bash
aws ssm start-session --target <instance-id> \
  --document-name AWS-StartPortForwardingSessionToRemoteHost \
  --parameters '{"host":["<aurora-endpoint>"],"portNumber":["5432"],"localPortNumber":["5432"]}'
```

### `extension "vector" is not available`

`init_db.py` crea la extensión. Si falla, el usuario de Aurora no tiene permiso
o la versión del motor no la trae. Comprueba a mano:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

### `ValidationException: The provided model identifier is invalid`

El modelo de `BEDROCK_LLM_MODEL` no está habilitado en tu cuenta, o el id está
mal escrito. Ver [setup.md §4](setup.md#4-habilitar-los-modelos-de-bedrock).

### `ThrottlingException` de Bedrock

Has ido demasiado rápido. Espera y reintenta. Las cuentas nuevas tienen cuotas
bajas; se suben por Service Quotas.

### El asistente responde "no encontré información relevante"

El corpus no está ingestado, o el umbral de similitud es muy alto. Comprueba
que la ingesta terminó y, si hace falta, baja `RETRIEVAL_MIN_SCORE` de `0.70` a
`0.5` en el `.env` del Lab 3.

---

## Cleanup

### `terraform destroy` falla con `BucketNotEmpty`

No debería: los buckets del lab llevan `force_destroy = true`. Si editaste eso,
vacíalo antes:

```bash
aws s3 rm "s3://<bucket>" --recursive
```

### `DependencyViolation` al borrar la VPC

Algo quedó dentro de la VPC que Terraform no gestiona — normalmente ENIs de DMS
que aún no se han liberado. Espera 5 minutos y repite el `destroy`.

### `InvalidParameterException: Cannot delete replication task in state running`

Para la tarea antes de destruir:

```bash
aws dms stop-replication-task \
  --replication-task-arn $(terraform -chdir=infra/lab1 output -raw dms_task_arn)
```

`scripts/cleanup.sh` del Lab 1 ya lo hace por ti.

### Quiero estar seguro de que no queda nada cobrando

```bash
for M in lab1 lab2 lab3; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
```

Los tres deben dar `0`. Y revisa a mano las consolas de
[RDS](https://console.aws.amazon.com/rds/),
[DMS](https://console.aws.amazon.com/dms/) y
[ElastiCache](https://console.aws.amazon.com/elasticache/) — son los servicios
que cuestan.

> El bucket del estado de Terraform y la tabla de locks **sobreviven** al
> cleanup a propósito. Cuestan prácticamente 0 (céntimos al mes).

---

## No está aquí

Abre un issue con:

- el comando exacto que ejecutaste
- el error completo (tapa los account id si quieres)
- salida de `./scripts/preflight.sh`
- en qué lab y en qué paso estabas
