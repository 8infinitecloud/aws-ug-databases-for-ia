# Revisión Well-Architected

Este workshop es un laboratorio, no una plataforma de producción. Varias
decisiones se apartan a propósito de lo que haría falta en producción, y
Well-Architected no pide perfección: pide **decisiones informadas**.

Esta página dice qué se cumple, qué se concede, y qué habría que cambiar el día
que esto dejara de ser un lab.

> Verificado con `checkov` (94 hallazgos abiertos, todos revisados abajo) y
> `terraform plan` contra una cuenta real.

---

## Seguridad

### Se cumple

| Práctica | Dónde |
|----------|-------|
| Cifrado en reposo en todas las bases de datos | `storage_encrypted = true` en RDS y Aurora |
| Cifrado en reposo en S3 y en el disco del EC2 | SSE-S3 en los tres buckets, `root_block_device.encrypted` |
| Sin credenciales en el código | contraseñas por `TF_VAR_`, nunca en fichero ni en `-var` |
| Credenciales en Secrets Manager | `aws_secretsmanager_secret` en Labs 1 y 3 |
| Bases de datos sin acceso público | subnets privadas, `publicly_accessible = false` |
| Acceso por security group, no por CIDR | Aurora y Redis solo aceptan desde el SG de la app |
| IMDSv2 obligatorio en el EC2 | `http_tokens = "required"` |
| SG por defecto de la VPC sin reglas | `aws_default_security_group` vacío en ambas VPC |
| Buckets sin acceso público | `block_public_acls` y compañía en los tres |
| IAM acotado a los recursos del lab | DMS y Glue solo a su bucket; Bedrock solo a los dos modelos |
| Sin acceso SSH | el despliegue va por SSM Run Command, no hay `key_name` (el 5432 de la base sí está abierto en la demo: ver concesiones) |
| State cifrado y privado | SSE-S3 + `block_public_*` en el bucket del state |

### Se concede, a propósito

| Concesión | Por qué | En producción |
|-----------|---------|---------------|
| Claves gestionadas por AWS, no CMK propias | Una CMK cuesta ~$1/mes y añade gestión de políticas al lab | KMS CMK con rotación y política explícita |
| Streamlit abierto a `0.0.0.0/0` en el 8501 | Hay que poder abrirlo desde el proyector de la sala | ALB con WAF, autenticación, y el puerto cerrado |
| Egress `0.0.0.0/0` en el SG de la app | Necesita Bedrock, S3, DynamoDB y `yum` | VPC endpoints y egress solo a ellos |
| Sin rotación automática de secretos | El lab vive dos horas | Rotación con Lambda |
| Sin VPC Flow Logs | Cuestan ingesta y almacenamiento de CloudWatch | Activados, con retención y alarma |
| Sin autenticación IAM en las bases de datos | Añade pasos al lab sin enseñar nada nuevo | IAM auth en vez de contraseña |
| `ec2:Describe*` con `Resource = "*"` en el rol de DMS | Esas acciones no admiten ARN de recurso | Igual: es inevitable, se acota con condiciones |
| La base del core en EC2 con IP pública | En la subred privada haría falta un NAT (~1 USD/día) o tres endpoints de interfaz solo para SSM | Subred privada con endpoints, o un *on-premises* de verdad tras VPN/Direct Connect |
| **El 5432 de la base, abierto a internet, con el superusuario** | Decisión explícita para la demo: conectarse con pgAdmin desde cualquier red sin túnel. Regla `0.0.0.0/0` en el SG y línea `0.0.0.0/0` en `pg_hba.conf`; sigue pidiendo contraseña (`scram-sha-256`, 24 caracteres aleatorios). Sin SSL: las consultas viajan en claro. **Es la concesión más arriesgada del repo**: un superusuario de PostgreSQL puede ejecutar comandos del sistema, y los escáneres encuentran un 5432 abierto en minutos | Cerrado: túnel de Session Manager (`terraform output db_tunnel_command`), o como mucho la IP de quien administra, un usuario de solo lectura y `hostssl` |
| Egress `0.0.0.0/0` en el SG de la base | Necesita `dnf`, Secrets Manager, S3, SSM y CloudWatch | VPC endpoints y egress solo a ellos |
| El usuario de la base es `SUPERUSER` y DMS lo usa | DMS sobre un PostgreSQL autogestionado necesita crear el slot; es el equivalente al usuario maestro de RDS | Un usuario solo con `REPLICATION` y `SELECT` sobre las tablas replicadas |
| Las claves de API de la demo van a `.demo_keys.json` | Las lee el generador de tráfico; el fichero está en `.gitignore` y con permisos 600 | Un gestor de secretos; y nunca en un portátil |

---

## Fiabilidad

### Se cumple

- **Aislamiento por lab**: cada uno tiene su propio state. Si el Lab 1 se rompe,
  el Lab 3 sigue desplegable.
- **Dos AZ** en los subnet groups de RDS, Aurora y ElastiCache.
- **Local Zones excluidas** del data source de AZs. Sin ese filtro, una cuenta
  dada de alta en `us-east-1-lim-1a` (Lima) podría intentar desplegar RDS ahí.
- **Dependencias explícitas** donde el orden importa: el EC2 espera a Aurora,
  Redis y el secret, porque su `user_data` necesita esos endpoints.
- **Reintentos ante throttling de Bedrock** con `tenacity` en el Lab 3.
- **Degradación elegante**: si Redis no responde, el RAG sigue funcionando sin
  memoria de sesión.
- **Recursos reemplazables**: `name_prefix` + `create_before_destroy` donde un
  cambio forzaría reemplazo con nombre fijo (parameter group, security groups).

### Se concede, a propósito

| Concesión | Por qué | En producción |
|-----------|---------|---------------|
| Single-AZ en RDS y en el EC2 | Multi-AZ duplica el coste del lab | Multi-AZ, y el EC2 en un ASG |
| `backup_retention_period = 1` en RDS | Es el mínimo que DMS necesita para el binlog de CDC | 7–35 días, con AWS Backup |
| `skip_final_snapshot = true` | El lab se crea y destruye muchas veces; un snapshot final cada vez estorba | `false`, siempre |
| `deletion_protection = false` | Con protección, el cleanup se queda a medias y el coste sigue corriendo | `true` |
| Sin point-in-time recovery en DynamoDB | El contenido se regenera ingiriendo otra vez | Activado |
| Aurora sin réplica de lectura | Un lab no tiene carga de lectura | Al menos una réplica |

---

## Excelencia operativa

### Se cumple

- **Todo es infraestructura como código.** No hay un solo paso de "y ahora
  entra a la consola y haz clic".
- **Comprobación previa**: `scripts/preflight.sh` valida herramientas,
  credenciales y acceso a Bedrock antes de crear nada.
- **Verificación posterior**: `scripts/validate_setup.py` y un `validate.sh` por
  lab, con checkpoints concretos en cada README.
- **CI que comprueba de verdad**: `terraform fmt` y `validate` en los 4 módulos,
  `shellcheck` en los 12 scripts, `ruff`, y validación del dataset.
- **State remoto con lock**, así que dos `apply` simultáneos no lo corrompen.
- **Etiquetado consistente** con `default_tags`: `Project`, `Environment`, `Lab`,
  `ManagedBy`.
- **Logs centralizados**: CloudWatch para el Glue Job y la tarea DMS.
- **Calidad de datos observable**: el reporte mide las 8 reglas en las tres
  etapas del pipeline y dice qué resolvió la transformación y qué el rechazo.

### Se concede, a propósito

| Concesión | Por qué | En producción |
|-----------|---------|---------------|
| Retención de logs de 7 días | Un lab no necesita más y CloudWatch cobra por GB | 30–400 días según el dato |
| Sin alarmas ni dashboards | No hay nada que vigilar en dos horas | CloudWatch alarms, y presupuesto con alerta |
| El Crawler se lanza a mano | En la demo interesa ver aparecer la tabla | `schedule` cron, o disparado por evento |
| Sin pipeline de despliegue por entornos | Hay un solo entorno: `lab` | dev → staging → prod con aprobaciones |

---

## Optimización de costes

### Se cumple

- **Coste documentado por lab**, con el escenario de olvidar el cleanup
  (~$5/día) y cómo poner un presupuesto con alerta: [COST_ESTIMATE.md](../COST_ESTIMATE.md).
- **Lo más pequeño que funciona**: `db.t3.micro`, `cache.t3.micro`, Aurora a
  0.5 ACU mínimo.
- **Glue Python Shell a 0.0625 DPU** en vez de PySpark: ~$0.0006 por ejecución,
  y arranca en 20 s en vez de 3 min.
- **Pago por uso donde se puede**: DynamoDB on-demand y Aurora Serverless v2
  cuestan ~0 sin tráfico.
- **Nova Pro en vez de Claude**: ~8× más barato para un lab que se repite.
- **Ciclo de vida en S3**: `raw/` a IA a los 90 días, `curated/` a los 180.
- **Cleanup que de verdad borra**: `force_destroy` en los buckets de datos y
  `deletion_protection = false`, para que un `destroy` no se quede a medias
  dejando recursos cobrando.
- **Verificación de que no queda nada**: los checkpoints de cada lab y el
  workflow de cleanup imprimen cuántos recursos quedan en cada state.

### Se concede, a propósito

- **Sin replicación cross-region** en los buckets: duplicaría el almacenamiento
  para proteger datos sintéticos que se regeneran.
- **Sin Savings Plans ni instancias reservadas**: no aplican a algo que vive dos
  horas.

---

## Eficiencia del rendimiento

### Se cumple

- **La herramienta acorde al tamaño del dato**: pandas para 1000 filas, no Spark.
  Elegir PySpark aquí sería el error de diseño, no lo contrario.
- **Parquet columnar y particionado** por `year`/`month` en la zona curada:
  Athena lee solo las particiones que necesita.
- **Índice HNSW en pgvector** para la búsqueda de similitud.
- **Caché de sesión en Redis** para no reconsultar en cada turno.
- **Aurora Serverless v2** escala con la carga sin intervención.

### Se concede, a propósito

- **Sin réplicas de lectura ni caché de Athena**: no hay concurrencia que lo
  justifique.
- **Embeddings de 1024 dimensiones**: Titan V2 admite 256 y 512, que serían más
  rápidas y baratas. Se usa 1024 por precisión, y cambiarlo obliga a reindexar.

---

## Sostenibilidad

- **Recursos mínimos** y **escalado a cero** donde el servicio lo permite
  (DynamoDB on-demand, Aurora Serverless, Glue por ejecución).
- **El cleanup es parte del workshop, no una nota al pie**: cada lab acaba
  recordándolo y el Lab 3 lo pone en primer plano.
- **Región única**, sin transferencia de datos entre regiones.
- **Parquet comprimido** ocupa una fracción del CSV equivalente.

---

## Hallazgos de `checkov` que quedan abiertos

Unos 100 hallazgos, todos revisados (el número exacto cambia con la versión de
`checkov`). Se agrupan en:

| Grupo | Nº aprox. | Decisión |
|-------|-----------|----------|
| KMS CMK en vez de claves gestionadas por AWS | ~12 | Concedido: coste y complejidad |
| Replicación cross-region y notificaciones de S3 | ~9 | Concedido: datos sintéticos |
| Multi-AZ, backups, deletion protection | ~10 | Concedido: es un lab que se destruye |
| Monitorización avanzada (Performance Insights, enhanced monitoring, flow logs) | ~10 | Concedido: coste sin beneficio en 2 h |
| Retención de logs ≥ 1 año | ~2 | Concedido: 7 días bastan |
| Rotación automática de secretos | ~2 | Concedido: el lab vive 2 h |
| `Resource = "*"` en acciones que no admiten ARN (`ec2:Describe*`) | ~4 | Inevitable |
| `zone_ids` en vez de `names` en el data source de AZs | 2 | Concedido: los nombres de AZ son por cuenta, pero para un lab de una sola cuenta da igual |
| Lambda fuera de VPC (Lab 4) | 1 | Concedido: sólo habla con DynamoDB, y meterla en la VPC exige un endpoint o NAT, coste y arranque en frío |
| Lambda sin DLQ (Lab 4) | 1 | Concedido **por diseño**: el scorer traga sus errores a propósito, porque reintentar un scoring ya contado inflaría los contadores y dispararía alertas falsas. Una DLQ contradiría esa decisión |
| Firma de código en Lambda | 1 | Concedido: el binario lo construye el propio `terraform apply` desde el repo |
| El bucket de artefactos de `payments` sin versionado, logging, ciclo de vida ni replicación | 6 | Concedido: guarda los `.sql` del esquema, que viven en el repo; se reconstruye con cada `apply` |
| EC2 de la base sin *detailed monitoring* (`CKV_AWS_126`) | 1 | Concedido: ~2 USD/mes por métricas cada minuto que el lab no mira. Las que importan —WAL retenido y disco— las publica la propia instancia |
| La tabla `terminal-state` del Lab 4 sin PITR ni CMK | 2 | Concedido: es el último latido de cada terminal y se reconstruye con el siguiente, en 30 s |
| Otros de endurecimiento avanzado | resto | Concedido |

### Lo que sí se corrigió, y por qué

Estos hallazgos no eran concesiones razonables y se arreglaron:

| Hallazgo | Qué se hizo | Por qué merecía la pena |
|---|---|---|
| `CKV_AWS_115` — sin límite de concurrencia | `reserved_concurrent_executions = 10` | Un POS con un bucle o una regla de IoT mal filtrada puede invocar miles de veces por minuto, y **cada invocación escribe en DynamoDB**. Un accidente cuesta céntimos en vez de una sorpresa |
| `CKV_AWS_50` — sin X-Ray | `tracing_config` en modo `Active` | En un lab *sobre latencia* no es burocracia: es lo que permite enseñar cuánto tarda de verdad el camino de reaccionar frente al de recordar |
| `CKV_AWS_240` — Firehose sin cifrar | `server_side_encryption` | Con clave gestionada por AWS es gratis. No había razón para no tenerlo |
| `CKV_AWS_135` — EC2 sin `ebs_optimized` (core) | `ebs_optimized = true` | Las `t4g` ya lo son, sin coste: no había nada que conceder |

Para reproducir el escaneo:

```bash
pip install checkov
checkov -d infra --framework terraform --compact
```

---

## Lo que cambiaría primero

Si este workshop se convirtiera en la base de algo real, en este orden:

1. **Cerrar Streamlit.** Hoy está abierto al mundo en el 8501. ALB + autenticación.
2. **KMS CMK** para S3, RDS, Aurora, DynamoDB y los secretos, con rotación.
3. **`deletion_protection = true` y `skip_final_snapshot = false`.** Lo contrario
   de lo que quiere un lab.
4. **Multi-AZ** en RDS, y el EC2 en un Auto Scaling Group detrás del ALB.
5. **VPC Flow Logs y CloudTrail** con retención y alarmas.
6. **Lake Formation** para control de acceso a nivel de fila y columna sobre el
   catálogo, que en el workshop solo se menciona.

---

## Auditoría de destruibilidad

Un taller que se despliega en la cuenta de otra persona tiene una obligación que
no aparece en los pilares: **todo lo que se crea tiene que poder borrarse**. Un
recurso que sobrevive al `destroy` es una factura que alguien descubre semanas
después.

Verificado el 2026-09-28 inspeccionando el plan en JSON, no el HCL — leer la
configuración con `grep` daba falsos positivos y falsos negativos. Revisado el
2026-10-02 al sustituir Aurora por PostgreSQL sobre EC2 en el core.

| Riesgo | Dónde aparecería | Estado |
|---|---|---|
| `prevent_destroy` | cualquier recurso | Sólo en el bucket de state de `bootstrap`, **a propósito** |
| `deletion_protection` | Aurora, ALB, DynamoDB | `false` en todos |
| `skip_final_snapshot = false` | Aurora (Lab 3) | `true`: no deja snapshots que cuesten |
| Disco que sobrevive a la instancia | EC2 del core | `delete_on_termination = true` en el volumen raíz |
| **Slot de replicación sin consumidor** | PostgreSQL del core | **No es un recurso de AWS y `destroy` no lo ve.** El `cleanup.sh` del Lab 1 lo borra por SSM tras destruir DMS. Si se olvida, el slot retiene WAL hasta llenar el disco del core; el tope `max_slot_wal_keep_size` lo invalida antes de llegar |
| `recovery_window_in_days` | Secrets Manager | **`0`** en los dos secretos — sin esto, un secreto queda 30 días en ventana de recuperación e **impide recrear el lab con el mismo nombre** |
| Bucket no vaciable | S3 | `force_destroy = true` en el lago (lab1), los documentos (lab3) y los artefactos del esquema (payments) |
| Repositorio con imágenes | ECR | `force_delete = true`: sin él, `destroy` falla en cuanto se sube la imagen del backend |
| Workgroup con resultados | Athena | `force_destroy = true` |
| EIP o NAT huérfanos | red | **No existen**: la concesión de `assign_public_ip` en Fargate evita el NAT Gateway, y de paso este problema |
| PITR / backups continuos | DynamoDB | Desactivado: son contadores que expiran en minutos |

### Verificado desplegando y destruyendo los seis módulos

El 2026-09-28 se aplicaron y destruyeron los seis módulos contra una cuenta real.
Tras el `destroy`, el barrido no encontró Aurora, snapshots, DMS, ALB, ECS, ECR,
Redis, EC2, secretos, EIP, NAT ni log groups del proyecto.

Dos cosas que la auditoría estática no habría cogido y que el ciclo completo sí:

- **Los log groups que crean los servicios**, no Terraform, sobrevivían al
  `destroy` con retención infinita. Son dos y aparecieron en momentos distintos:
  `/aws/rds/cluster/<id>/postgresql`, que crea RDS al exportar logs, y
  `dms-tasks-<instancia>`, que crea DMS al escribir los de la tarea. Los dos
  tienen nombre determinista, así que ahora los declara Terraform —en
  `payments`, `lab3` y `lab1`— con retención y se van con el resto.

  El patrón merece recordarse: **si un servicio de AWS crea un recurso por su
  cuenta, `terraform destroy` no se lo lleva.** Se descubre haciendo el ciclo
  completo, no leyendo la configuración.
- **Quedan tres log groups de la era CloudFormation**
  (`/aws/rds/cluster/financial-compliance-rag-lab-auroracluster-*`, de mayo de
  2026, ~26 KB en total). No los creó ningún módulo actual; son restos de
  despliegues anteriores a la migración a Terraform. Borrarlos es seguro:

  ```bash
  aws logs describe-log-groups \
    --query 'logGroups[?contains(logGroupName,`financial-compliance`)].logGroupName' \
    --output text | tr '\t' '\n' | xargs -I{} aws logs delete-log-group --log-group-name {}
  ```

**Lo único que sobrevive a propósito** es `fincorp-datalake-tfstate-<account>`,
que guarda el state de todo lo demás y cuesta céntimos al mes. Tiene
`prevent_destroy` para que un `destroy` distraído no se lleve por delante la
capacidad de destruir el resto.

### Orden de destrucción

No es arbitrario:

```
lab4 → lab2 → lab1 → lab3 → payments
```

- **lab4 primero**: su Firehose escribe la telemetría en el bucket del lab1
- **lab2 antes que lab1**: escribe en su bucket
- **lab1 antes que payments**: las ENIs de DMS bloquean el borrado de las
  subnets, y su `cleanup.sh` necesita el core vivo para borrar el slot de
  replicación
- **lab3 en cualquier momento**: es independiente
