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

El `LOCK_ID` sale en el propio mensaje de error. Si el error no lo muestra, el
lock está en la tabla de locks:

```bash
aws dynamodb scan --table-name fincorp-datalake-tflock \
  --query 'Items[?Info].[LockID.S, Info.S]' --output text
```

Un `plan` o `apply` interrumpido con Ctrl+C también deja el lock: el campo
`Operation` lo dice (`OperationTypePlan`).

### `Error: No value for required variable` (`state_bucket`, `db_password`)

Al ejecutar `terraform` a mano, sin los scripts del lab. Los módulos `lab2`,
`lab3`, `lab4` leen otros states y necesitan saber dónde viven; `payments` y
`lab3` necesitan la contraseña de su base incluso para `destroy`:

```bash
export TF_VAR_state_bucket=$(sed -n 's/^bucket *= *"\(.*\)"/\1/p' infra/backend.hcl)
export TF_VAR_db_password='...'
```

### `failed to upload state: ... request stream is not seekable`

Un corte de red al final de un `apply` o `destroy`: los recursos cambiaron pero
el state remoto no se actualizó. Vuelve a ejecutar el mismo comando: Terraform
compara con lo que existe y termina el trabajo.

---

## El core (`infra/payments`)

### `/health` dice `database: unreachable` tras desplegar

La base de EC2 instala PostgreSQL y aplica el esquema en su **primer arranque**:
unos minutos. `deploy_core.sh` espera a que `/health` diga `database: ok`. Si no
llega, mira el log del arranque por Session Manager:

```bash
aws ssm start-session --target "$(terraform -chdir=infra/payments output -raw db_instance_id)"
sudo tail -100 /var/log/fincorp-db-init.log
```

### Cambié un `.sql` y la base no lo tiene

El esquema solo se aplica solo en el **primer** arranque. Para reaplicarlo:

```bash
terraform -chdir=infra/payments apply   # sube los ficheros nuevos al bucket
./scripts/apply_schema.sh               # la instancia los reaplica, por SSM
```

Todos los `.sql` son idempotentes.

### `aws ssm start-session`: `SessionManagerPlugin is not found`

El túnel necesita el plugin de Session Manager instalado en tu máquina. Para las
claves de la demo no hace falta: `python scripts/provision_demo_keys.py --ssm`
va por SSM Run Command, sin túnel.

---

## Lab 1 — Ingestion + Data Lake

### `terraform apply` tarda muchísimo

Es normal. La instancia de replicación de DMS tarda ~10 minutos. No la
interrumpas: un `apply` a medias deja recursos huérfanos que hay que limpiar a
mano.

### DMS: `could not access file "test_decoding"`

Falta el plugin de decodificación lógica con el que DMS lee el WAL. En Amazon
Linux 2023 viene en **`postgresql16-contrib`**, que el arranque de la base
instala por esto. Si ves este error, la instancia se creó con un script anterior:
reemplázala (`terraform apply -replace=aws_instance.db`) o instala el paquete y
reinicia PostgreSQL.

Se encontró ejecutando el arranque en un contenedor de Amazon Linux 2023, antes
de desplegar: crear un slot lógico fallaba con este mensaje.

### Faltan columnas en `raw/` (`line_items`, `metadata`, `payload`...)

Para DMS, el `text` y el `jsonb` de PostgreSQL son **LOBs**, y con
`SupportLobs = false` los **descarta sin avisar**. La tarea los tiene activados
en modo limitado (32 KB). Si ves columnas de menos, revisa
`replication_task_settings` en `infra/lab1/dms.tf`.

Ya pasó una vez, con `risk_level`, `payment_method` y `status`: el CSV llegó con
16 columnas en vez de 19 y el Lab 2 murió con `KeyError`.

### DMS: `No tables were found at task initialization`

**DMS no puede replicar vistas de PostgreSQL**, ni con full load ni con CDC. La
tarea muere en menos de un segundo, antes de consultar nada. Diagnosticado el
2026-09-28.

Por eso la tarea replica las **tablas base** (`charges`, `merchants`...) y Glue
hace el JOIN que hacía la vista. Si ves este error, revisa que los
`object-locator` de `table_mappings` apunten a tablas que existen en el esquema
`acquiring`.

### DMS ignora `EnableLogging` y no escribe ningún log

DMS asume un rol llamado **exactamente** `dms-cloudwatch-logs-role` para escribir
los logs de la tarea. Si no existe, no avisa: el ajuste dice que loguea, la tarea
corre, y no aparece ningún log group.

Eso convierte cualquier fallo en indiagnosticable. Es el segundo rol de cuenta
con nombre cableado que DMS exige, junto a `dms-vpc-role`; los crea
`infra/lab1/dms.tf` salvo que pongas `create_dms_vpc_role = false`.

```bash
aws iam get-role --role-name dms-cloudwatch-logs-role
aws logs describe-log-groups --query 'logGroups[?contains(logGroupName,`dms-tasks`)].logGroupName'
```

### `Test connection ... should be successful for starting the replication task`

DMS exige que **ambos** endpoints tengan una prueba de conexión correcta antes de
arrancar la tarea:

```bash
RI=$(aws dms describe-replication-instances --query 'ReplicationInstances[0].ReplicationInstanceArn' --output text)
for E in source-core target-s3; do
  EP=$(aws dms describe-endpoints --query "Endpoints[?EndpointIdentifier=='fincorp-datalake-lab-$E'].EndpointArn" --output text)
  aws dms test-connection --replication-instance-arn "$RI" --endpoint-arn "$EP"
done
aws dms describe-connections --query 'Connections[*].[EndpointIdentifier,Status]' --output text
```

Si falla el origen: la base todavía arranca, la contraseña cambió después de crear
el endpoint, o el `pg_hba.conf` no admite la VPC (lo escribe el arranque de la
instancia).

### DMS no puede escribir en S3: `Failed to connect to database`

Mensaje engañoso: no es la base, es la red. La instancia de replicación vive en
las subnets privadas de `payments`, que **no tienen NAT Gateway**. Lo resuelve el
**Gateway Endpoint de S3** de `infra/payments/network.tf`: gratis, y el tráfico
no sale de la red de AWS.

### Un pago no aparece en `raw/`

El CDC escribe un fichero de cambios cada **~30 s** (`cdc_max_batch_interval`), en
`raw/acquiring/<tabla>/AAAA/MM/DD/`. Espera un minuto antes de preocuparte.

```bash
aws dms describe-replication-tasks   --query "ReplicationTasks[].[ReplicationTaskIdentifier,Status,ReplicationTaskStats.FullLoadProgressPercent]"   --output table
```

Tiene que estar en `running`. Si está en `stopped`, `setup.sh` la retoma donde
lo dejó. `validate.sh` hace un pago real y espera a verlo llegar.

**Las carpetas de fecha van en UTC.** Desde las 19:00 de Lima, los cambios caen
en la carpeta del día siguiente (`…/2026/10/03/` cuando en Lima aún es el 2).

Para ver el CDC sin buscar ficheros: consola de DMS → la tarea → *Table
statistics*. Las columnas *Inserts* y *Updates* suben ~30 s después de cada pago.
`Table completed` solo indica que la foto inicial de esa tabla terminó.

### La alarma `wal-retenido` está en `ALARM`

Un slot de replicación retiene WAL sin que nadie lo consuma: la tarea DMS está
parada o se destruyó sin borrar su slot. Si la tarea no va a volver, bórralo:

```bash
source scripts/lib/tf.sh && source scripts/lib/core.sh
core_sql "SELECT slot_name, active, pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots"
core_drop_inactive_slots
```

Si se deja, PostgreSQL lo invalida al llegar a `max_slot_wal_keep_size` (4 GB),
antes de llenar el disco. El CDC habría que recargarlo; el core sigue vivo.

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

**Eso es lo esperado en `raw`.** El dataset legacy trae problemas deliberados
para que las reglas los detecten. En `curated` las 10 pasan, y los rechazados
(~137, todos `source = legacy`) quedan en `rejected/`.

### Athena: `Only one sql statement is allowed`

El texto tiene más de una sentencia. Un comentario después de un `;` cuenta
como una segunda. Ejecuta una sola consulta, sin `;` seguido de más texto.

### Athena: `TABLE_NOT_FOUND ... financial_platform.telemetry`

Las consultas guardadas 6 a 8 leen la telemetría del Lab 4. Despliega el Lab 4,
deja que llegue telemetría a `raw/telemetry/` (~1 min) y vuelve a ejecutar el
crawler: `./labs/02-transformation-quality-catalog/scripts/run_job.sh`.

### El Crawler no crea la tabla `transactions`

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

### `deploy-app.sh`: `.env no encontrado. El UserData del EC2 no completó`

`deploy-app.sh` se ejecutó mientras el EC2 todavía arrancaba. El script espera a
`cloud-init`; si aun así falla, comprueba el arranque y vuelve a ejecutarlo:

```bash
aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript \
  --parameters 'commands=["cloud-init status --long","tail -20 /var/log/user-data.log"]'
```

### `could not connect to server: Connection timed out` contra Aurora

Aurora vive en subnets privadas: **no es alcanzable desde tu laptop**, por
diseño. `deploy-app.sh` y `demo_cierre.sh` ya ejecutan la inicialización y la
ingesta en el EC2. Para conectarte tú, un túnel SSM (necesita el
`session-manager-plugin`):

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
que la ingesta terminó y, si hace falta, baja `RETRIEVAL_MIN_SCORE` de `0.60` a
`0.5` en el `.env` del Lab 3.

Si es una pregunta corta que **sí** está en el corpus, mira el score real: el
retriever acepta `min_score=0.0` y devuelve todo ordenado. Con el umbral antiguo
(0.70), "¿qué comercios hicieron pagos hoy?" dejaba el documento correcto el
primero con 0.687 y lo descartaba.

### El asistente dice que hoy no hay pagos, y sí los hay

Por orden:

1. **No se ejecutó `demo_cierre.sh` después de los pagos.** El RAG no se
   actualiza solo.
2. **Los pagos aún no estaban en `raw/`.** Espera ~30 s después del último pago
   antes de ejecutarlo.
3. **"Hoy" es el día de Lima**, no el UTC.

Comprueba el documento que se ingestó:

```bash
aws ssm send-command --instance-ids <instance-id> --document-name AWS-RunShellScript \
  --parameters 'commands=["sed -n 1,40p /opt/rag-lab/data/derived/operaciones-actividad-hoy.md"]'
```

### `demo_cierre.sh`: `La ingesta termino en Failed`

La salida muestra las últimas líneas del error. Las causas habituales: el Lab 3
sin `deploy-app.sh` (no está el script en el EC2) o el EC2 sin permiso de
lectura sobre el bucket del Lab 1 (`terraform apply` de `infra/lab3` después de
crear el Lab 1).

---

## Terminal Core2 y pgAdmin

### El Core2: `wifi: desconectado, reintento 8/8`

El punto de acceso no está o rechaza la conexión. El ESP32 solo ve redes de
**2,4 GHz**: en el hotspot de un iPhone activa *Maximizar compatibilidad*. Las
credenciales están en `payments/firmware/sdkconfig` (`CONFIG_WIFI_SSID`,
`CONFIG_WIFI_PASSWORD`).

### El Core2: `couldn't get hostname` o `ESP_ERR_HTTP_CONNECT`

Sin WiFi, o con la URL de un despliegue anterior. Cada `infra/payments` nuevo
tiene otra URL de API Gateway: actualiza `CONFIG_API_BASE_URL` y vuelve a
grabar. Ver [payments/firmware/README.md](../payments/firmware/README.md#después-de-redesplegar-el-core).

### pgAdmin: el Query Tool se queda en blanco o `connection to the server has been lost`

Si la conexión aparece en la base (`SELECT * FROM pg_stat_activity WHERE
application_name LIKE 'pgAdmin%'`) pero el Query Tool no muestra nada, el fallo
es de pgAdmin, no de la base. pgAdmin 9.1 sobre Python 3.13 registra
`urlopen() got an unexpected keyword argument 'cafile'` en
`~/.pgadmin/pgadmin4.log`. Actualiza pgAdmin y borra sus sesiones:

```bash
rm -rf ~/.pgadmin/sessions
brew install --cask pgadmin4      # o descarga la última de pgadmin.org
```

Los servidores guardados están en `~/.pgadmin/pgadmin4.db` y se conservan.

Si la conexión se corta tras un rato sin uso, la base tiene keepalives TCP
(60 s) para que el NAT no deje conexiones medio cerradas.

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
for M in payments lab1 lab2 lab3 lab4; do
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

## Probar sin desplegar

Antes de gastar 35 minutos y dinero en AWS, casi todo se puede verificar en local:

```bash
pip install -r requirements.txt

# El pipeline COMPLETO del Lab 2 sin AWS: Parquet, rejected/ y quality report
python scripts/run_pipeline_local.py --keep

# Los tests
pytest tests/ -v

# La infraestructura compila y el grafo resuelve
for M in bootstrap payments lab1 lab2 lab3 lab4; do
  terraform -chdir="infra/$M" init -backend=false
  terraform -chdir="infra/$M" validate
done

# Qué se crearía en tu cuenta (solo lectura, no cuesta nada)
terraform -chdir=infra/lab1 plan

# Escaneo de seguridad
pip install checkov && checkov -d infra --framework terraform --compact
```

Detalle completo de qué es local y qué no: [local-development.md](local-development.md).

---

## No está aquí

Abre un issue con:

- el comando exacto que ejecutaste
- el error completo (tapa los account id si quieres)
- salida de `./scripts/preflight.sh`
- en qué lab y en qué paso estabas
