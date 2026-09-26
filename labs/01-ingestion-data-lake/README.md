# Lab 1 — Ingestion + Data Lake

> **Lab 1 de 3** · [← Índice del workshop](../../README.md)

| | |
|---|---|
| **Tiempo** | ~25 min (de los cuales ~15 son espera de AWS) |
| **Coste** | ~$0.09/hora mientras está desplegado · ~$0.18 por una sesión de 2 h |
| **Qué despliegas** | VPC, RDS MySQL, AWS DMS, bucket S3 del data lake |
| **Requisitos** | `./scripts/preflight.sh` en verde y el backend de Terraform creado |

**Objetivo:** llevar datos de una base de datos de producción a un data lake en
S3, sin escribir un ETL y sin tocar la base de datos origen.

**Qué te llevas:** entender por qué la ingesta es un problema aparte del
análisis, y por qué un data lake tiene zonas (`raw/`, `curated/`) en lugar de
una sola carpeta.

---

## El Problema

FinCorp tiene datos transaccionales críticos atrapados en una base de datos MySQL. El equipo de Analytics necesita acceder a esas transacciones sin afectar la base de datos de producción. El equipo de IA necesita ese mismo dataset para entrenar modelos y alimentar un asistente de compliance.

**El problema no es la IA. Es que los datos están en un solo lugar y no pueden ser consumidos de forma segura por múltiples equipos.**

```
ANTES:
  App de Facturación
       │
  RDS MySQL (producción)
       │
  Consultas directas desde Analytics → degrada performance de producción
  Consultas directas desde IA       → mismo problema

DESPUÉS:
  RDS MySQL (producción)
       │ AWS DMS (replicación)
       ▼
  Amazon S3 — raw/
       │
  Analytics + IA (sin afectar producción)
```

---

## La Decisión de Arquitectura

### ¿Por qué AWS DMS?

Database Migration Service replica datos desde la fuente (MySQL) al destino (S3) con dos modos:

- **Full Load**: copia inicial de todos los datos existentes (~2 min para nuestro dataset)
- **CDC (Change Data Capture)**: captura cambios en tiempo real leyendo el binlog de MySQL

En el demo mostramos el Full Load. El CDC está configurado para que la audiencia vea que los cambios futuros también se replican.

### ¿Por qué S3 como destino?

- Un solo bucket puede tener múltiples "zonas" (`raw/`, `curated/`) separadas por prefijos
- Costo mínimo: ~$0.023/GB/mes para Standard
- Desacoplado de la fuente: los consumidores leen de S3, nunca de RDS directamente
- Base natural para el Lab 2 (Glue lee de S3) y el Lab 3 (ingesta de documentos)

### Estructura del Data Lake

```
s3://fincorp-datalake-{account-id}/
├── raw/                          ← DMS escribe aquí (datos originales, sin transformar)
│   ├── transactions/
│   │   └── LOAD00000001.csv
│   └── customers/
│       └── LOAD00000001.csv
├── curated/                      ← Output del Lab 2 (Glue)
│   └── transactions/
│       └── year=2026/month=09/
└── staging/                      ← CSV de ejemplo para carga manual
    ├── transactions/
    └── customers/
```

---

## Arquitectura

```
┌─────────────────┐
│  RDS MySQL 8.0  │  ← Fuente de datos transaccional FinCorp
│  db.t3.micro    │    (~1000 transacciones, ~200 clientes)
└────────┬────────┘
         │ binlog (CDC) + Full Load
         ▼
┌─────────────────────────────────────┐
│         AWS DMS                     │
│  Replication Instance: dms.t3.medium│
│  Migration Type: Full Load + CDC    │
│  Source → MySQL endpoint            │
│  Target → S3 endpoint               │
└────────────────────┬────────────────┘
                     │ CSV
                     ▼
┌─────────────────────────────────────┐
│         Amazon S3                   │
│  fincorp-datalake-{account-id}      │
│                                     │
│  raw/transactions/LOAD*.csv         │
│  raw/customers/LOAD*.csv            │
└─────────────────────────────────────┘
         │
         └─► Lab 2: Glue lee raw/ y escribe curated/
```

---

## Dataset

### transactions.csv (~1000 filas)

| Campo | Tipo | Descripción |
|-------|------|-------------|
| `transaction_id` | VARCHAR(20) | ID único de la transacción |
| `customer_id` | VARCHAR(10) | Referencia a customers |
| `amount` | DECIMAL(12,2) | Monto — **NULL en ~5% de filas** |
| `currency` | VARCHAR(3) | USD, PEN, EUR |
| `transaction_type` | VARCHAR(30) | DEPOSIT/WITHDRAWAL/TRANSFER — **valores en español en ~2%** |
| `status` | VARCHAR(20) | PENDING/COMPLETED/FAILED/PROCESSING — **case inconsistente en ~8%** |
| `transaction_date` | VARCHAR(20) | **Mezcla de YYYY-MM-DD y DD/MM/YYYY (~10%)** |
| `branch_id` | VARCHAR(10) | BR001-BR020 |

### customers.csv (~200 filas)

Tabla de referencia limpia. Sin problemas deliberados. Campos: `customer_id`, `name`, `email`, `country`, `risk_level`, `onboarding_date`.

### Problemas deliberados (para Lab 2 Data Quality)

| Campo | Problema | Porcentaje | Regla DQ que lo detecta |
|-------|---------|-----------|------------------------|
| `amount` | NULL | ~5% (~50 filas) | `Completeness "amount" >= 0.95` |
| `transaction_id` | Duplicado | ~3% (~30 filas) | `Uniqueness "transaction_id" = 1.0` |
| `transaction_date` | Formato DD/MM/YYYY | ~10% (~100 filas) | Regex check en Glue job |
| `transaction_type` | Valores en español | ~2% (~20 filas) | `ColumnValues "transaction_type" in [...]` |
| `status` | Case inconsistente | ~8% (~80 filas) | `ColumnValues "status" in [...]` |

---

## Prerrequisitos

- AWS CLI v2 configurado (`aws configure`)
- `jq` instalado (`brew install jq` en macOS / `apt install jq` en Linux)
- Terraform >= 1.6 y el backend remoto bootstrapeado (ver `infra/README.md`)
- Permisos IAM: RDS, DMS, S3, Secrets Manager, IAM, EC2
- `mysql` client (opcional — para ejecutar seed.sql directamente)
- Región: `us-east-1` (u otra con DMS disponible)

### Permisos IAM mínimos

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "rds:*",
        "dms:*",
        "s3:*",
        "secretsmanager:*",
        "iam:CreateRole", "iam:AttachRolePolicy", "iam:PassRole",
        "ec2:*"
      ],
      "Resource": "*"
    }
  ]
}
```

---

## Deployment

### Paso 1: Setup de infraestructura (~15 min)

```bash
cd labs/01-ingestion-data-lake/scripts

export TF_VAR_db_password='CambiaEstaPassword2026!'   # mínimo 8 caracteres
export AWS_REGION="us-east-1"

./setup.sh
```

Si no exportas la contraseña, el script te la pide por teclado.

El script hace:
1. Inicializa Terraform contra el backend remoto
2. Despliega el stack (VPC + RDS + DMS + S3)
3. Sube los CSV a `s3://bucket/staging/`
4. Intenta ejecutar `seed.sql` en RDS (requiere conectividad a la subnet privada)

> **Nota:** RDS está en una subnet privada. Si ejecutas el script desde fuera de la VPC, el paso de seed.sql mostrará un warning. Para sembrar la BD:
> - Opción A: Crea un EC2 en la subnet pública y ejecuta seed.sql desde ahí
> - Opción B: Usa AWS Cloud9 dentro de la misma VPC
> - Opción C: Configura SSM Session Manager con un bastion

### Paso 2: Iniciar la tarea DMS

Desde la consola AWS: **Database Migration Service → Replication tasks → fincorp-datalake-lab-task → Start/Resume**

O desde CLI:

```bash
# Obtener ARN de la tarea desde los outputs de Terraform
TASK_ARN=$(terraform -chdir=../../../infra/lab1 output -raw dms_task_arn)

aws dms start-replication-task \
  --replication-task-arn "$TASK_ARN" \
  --start-replication-task-type start-replication
```

### Paso 3: Validar el resultado

```bash
./validate.sh
```

Salida esperada:

```
[PASS] Modulo infra/lab1 desplegado
[PASS] DMS Task status: load-complete | Full Load: 100%
[PASS] Archivos transactions encontrados: 1 CSV(s)
[PASS] Archivos customers encontrados: 1 CSV(s)
[PASS] transactions: ~1000 filas (esperado: ~1000)
✅ Validación: PASS (0 fallos)
```

---

## ✅ Checkpoint — ¿funcionó?

Antes de pasar al Lab 2, estas tres cosas tienen que ser ciertas:

```bash
# 1. El módulo tiene recursos
terraform -chdir=../../../infra/lab1 state list | wc -l      # > 20

# 2. Hay CSV en la zona raw del data lake
BUCKET=$(terraform -chdir=../../../infra/lab1 output -raw s3_bucket_name)
aws s3 ls "s3://$BUCKET/raw/" --recursive                     # al menos 2 ficheros

# 3. La tarea DMS terminó el full load
aws dms describe-replication-tasks \
  --query "ReplicationTasks[0].[Status,ReplicationTaskStats.FullLoadProgressPercent]" \
  --output text                                               # running|stopped  100
```

**Abre un CSV de `raw/` y míralo.** Vas a ver importes vacíos, estados en
minúsculas y tipos de transacción que no existen. Eso es el punto de partida del
Lab 2 — no lo arregles ahora.

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md#lab-1--ingestion--data-lake)

---

## Outputs esperados

| Output | Valor típico |
|--------|-------------|
| RDS Endpoint | `fincorp-datalake-lab-mysql.xxxx.us-east-1.rds.amazonaws.com` |
| S3 Bucket | `fincorp-datalake-{account-id}` |
| S3 raw/transactions | `LOAD00000001.csv` (~1000 filas) |
| S3 raw/customers | `LOAD00000001.csv` (~200 filas) |
| DMS Task Status | `load-complete` |

---

## Cleanup

```bash
./cleanup.sh
```

Elimina: DMS task + endpoints + instance, RDS, VPC, S3 (incluyendo objetos versionados), Secrets Manager, IAM Role — todo el módulo `infra/lab1`.

**Costo estimado si se olvida el cleanup:** ~$0.09/hr = ~$2.16/día
- RDS MySQL db.t3.micro: ~$0.017/hr
- DMS dms.t3.medium: ~$0.068/hr
- S3: <$0.01 para el dataset del lab

---

## Conexión con Lab 2

Los archivos en `s3://bucket/raw/transactions/LOAD*.csv` son la entrada del Lab 2. Contienen los **problemas deliberados** documentados arriba, que las reglas de Glue Data Quality detectarán.

```
Lab 1 Output                   Lab 2 Input
────────────────────────────────────────────
s3://bucket/raw/transactions/  →  Glue job lee raw/
s3://bucket/raw/customers/     →  Glue job lee raw/
                               →  Aplica DQ rules
                               →  Escribe s3://bucket/curated/
```

→ **Continúa en:** `labs/02-transformation-quality-catalog/README.md`

---

## Casos reales que aplican este patrón

### AJE Group (empresa peruana)

AJE tenía datos de ventas, distribución e inventario distribuidos en múltiples ERPs por país. La arquitectura que implementaron sigue exactamente el mismo patrón de este lab:

```
ERP locales (múltiples países)
    │ AWS DMS — migración inicial + CDC
    ▼
Amazon RDS / Amazon S3 (zona de aterrizaje)
    │
    ▼ AWS Glue
Amazon Redshift (Data Warehouse)
    │
    ▼ Amazon QuickSight
Analytics consolidado de 24 países
```

**El punto:** AJE no empezó con IA. Empezó con el problema básico que estamos resolviendo en este lab — llevar datos distribuidos a un lugar centralizado y confiable.

### BMW Group

BMW consolidó datos de más de 3,000 sistemas (vehículos, manufactura, clientes) usando el mismo patrón de Data Lake en S3 como zona de aterrizaje neutral, con DMS para la ingestion desde fuentes relacionales.

```
3,000+ sistemas fuente
    │ AWS DMS + Amazon Kinesis
    ▼
Amazon S3 — Cloud Data Hub
(zona raw — datos sin transformar)
    │
    ▼ [siguiente capa: Lab 2]
Glue + Redshift + SageMaker
```

**Referencia oficial:** [AWS Case Study — BMW Group](https://aws.amazon.com/solutions/case-studies/bmw-group/)

> *"Implementamos una versión simplificada de los mismos patrones arquitectónicos. El problema que resolvemos en este lab — centralizar datos distribuidos en una zona de aterrizaje confiable — es exactamente el punto de partida de BMW y AJE."*


---

## Siguiente paso

Ya tienes datos crudos en el data lake. Están sucios y nadie sabe qué
significan — eso es exactamente lo que resuelve el Lab 2.

**→ [Lab 2 — Transformation + Quality + Catalog](../02-transformation-quality-catalog/README.md)**

> ¿Paras aquí por hoy? Haz cleanup para no pagar de más:
> `bash scripts/cleanup.sh`

| [← Índice](../../README.md) | [Costes](../../COST_ESTIMATE.md) | [Troubleshooting](../../docs/troubleshooting.md) |
|---|---|---|
