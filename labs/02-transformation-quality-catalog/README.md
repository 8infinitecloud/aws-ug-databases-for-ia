# Lab 2 — Transformation + Data Quality + Catalog

> **Lab 2 de 3** · [← Lab 1](../01-ingestion-data-lake/README.md) · [Índice](../../README.md)

| | |
|---|---|
| **Tiempo** | ~15 min (el job tarda ~3) |
| **Coste** | ~$0.01 en total · **$0 en reposo** (Glue solo cobra cuando corre) |
| **Qué despliegas** | Glue Job, Glue Crawler, Glue Data Catalog, DQ Ruleset |
| **Requisitos** | **El Lab 1 desplegado** — este lab lee su bucket |

**Objetivo:** convertir los CSV sucios de `raw/` en Parquet confiable y
catalogado, apartando los registros que no pasan las reglas de calidad.

**Qué te llevas:** que "calidad de datos" no es limpiar y callar, sino medir,
documentar y **conservar lo que se rechazó**. Y que un catálogo es lo que hace
que un fichero en S3 sea un dato con significado.

---

## El Problema

Después del Lab 1, tenemos datos en S3. Pero tener datos no es lo mismo que poder confiar en ellos.

El dataset de FinCorp llegó de producción con problemas reales:
- Montos sin registrar (NULL)
- Transacciones duplicadas
- Fechas en formatos inconsistentes
- Tipos de transacción en español cuando el sistema espera inglés
- Estados con case inconsistente

**¿Puedes alimentar una IA con estos datos? Técnicamente, sí. ¿Deberías? No.**

El Lab 2 responde la pregunta: *"¿Podemos confiar en estos datos?"*

```
ANTES (datos raw del Lab 1):
  - "DEPÓSITO", "pending", "15/03/2025", amount=NULL
  - Duplicados silenciosos

DESPUÉS (datos curados del Lab 2):
  - "DEPOSIT", "PENDING", "2025-03-15", amount validado
  - Sin duplicados, con quality score documentado
  - Metadata de negocio en el Glue Catalog
```

---

## La Decisión de Arquitectura

### ¿Por qué Glue Python Shell (no PySpark)?

Para ~1000 filas, PySpark sería overkill. Python Shell inicia en ~20 segundos vs 3 minutos de Spark, y el código es pandas — la librería que todos conocen.

En producción con millones de filas, migrar a PySpark Glue Job cambia solo el `command.name = "glueetl"` en `infra/lab2/glue.tf` y los DataFrames de pandas a Spark. La lógica de negocio queda igual.

### ¿Por qué Glue Data Quality con DQDL?

DQDL (Data Quality Definition Language) es declarativo y legible:
```
Completeness "amount" >= 0.95
Uniqueness "transaction_id" = 1.0
ColumnValues "transaction_type" in ["DEPOSIT", "WITHDRAWAL", "TRANSFER"]
```

Cualquiera puede leer estas reglas y entender qué se está validando. No necesitas saber programar para definir expectativas sobre datos.

### ¿Por qué Glue Data Catalog?

El Crawler auto-descubre el schema técnico (columnas, tipos, particiones). Pero el Catalog también guarda metadata de negocio: quién es el owner, de dónde vienen los datos, cuál es el quality score. La diferencia entre "tengo una tabla" y "entiendo mis datos".

---

## Arquitectura

```
S3 raw/transactions/        ← Output del Lab 1 (DMS)
    │
    ▼
┌─────────────────────────────────────┐
│   AWS Glue Job (Python Shell)       │
│                                     │
│   [1] Lee CSV raw                   │
│   [2] Normaliza fechas/tipos/status │
│   [3] Evalúa Data Quality           │
│   [4] Separa válidos vs rechazados  │
│   [5] Escribe Parquet particionado  │
│   [6] Genera quality_report.json   │
└──────────┬──────────────┬───────────┘
           │              │
           ▼              ▼
    S3 curated/     S3 rejected/
    (Parquet)       (CSV con razón)
           │
           ▼
┌─────────────────────────────────────┐
│   Glue Crawler                      │
│   Descubre schema automáticamente   │
└──────────┬──────────────────────────┘
           │
           ▼
┌─────────────────────────────────────┐
│   Glue Data Catalog                 │
│   Database: financial_platform      │
│   Table: transactions              │
│   + Metadata de negocio             │
└─────────────────────────────────────┘
           │
           ▼
    Amazon Athena            Lab 3 (RAG)
    (SQL sobre S3)           (embeddings)
```

---

## Reglas de Calidad (DQDL)

El job mide las **10 reglas tres veces**, una por etapa del pipeline. Sin las tres
no se puede distinguir qué arregló la normalización de qué se arregló apartando
filas, y esa distinción es la lección del lab.

**Ocho reglas validan columnas. Las dos últimas comparan campos entre sí**, y son
las propias de un adquirente: ninguna herramienta que mire columna a columna las
detecta.

| ID | Regla | Campo | raw | transf. | curated | resuelto por |
|----|-------|-------|-----|---------|---------|--------------|
| R01 | Completeness | `amount` >= 0.95 | ✅ 0.9500 | ✅ | ✅ | — |
| R02 | IsComplete | `transaction_id` | ✅ | ✅ | ✅ | — |
| R03 | IsComplete | `customer_id` | ✅ | ✅ | ✅ | — |
| R04 | Uniqueness | `transaction_id` | ❌ | ❌ | ✅ | **rechazo** |
| R05 | ColumnValues | `payment_method` | ❌ | ✅ | ✅ | **transformación** |
| R06 | ColumnValues | `status` | ❌ | ✅ | ✅ | **transformación** |
| R07 | ColumnValues | `currency` | ✅ | ✅ | ✅ | — |
| R08 | ColumnLength | `transaction_id` | ✅ | ✅ | ✅ | — |
| **R09** | **CustomSql** | `net = amount - fee` | ❌ | ❌ | ✅ | **rechazo** |
| **R10** | **CustomSql** | `CAPTURED` ⇒ `response_code = 00` | ❌ | ❌ | ✅ | **rechazo** |

**Total: 5/10 en raw → 7/10 tras transformar → 10/10 en curated.**
**1000 operaciones → 863 válidas / 137 apartadas.**

### Dos clases de problema, y por qué importa la diferencia

**Problemas de forma** (R05, R06) — el dato es recuperable. `TARJETA` es
inequívocamente `CARD_PRESENT`; `capturada` es `CAPTURED`. Se normaliza y la fila
se conserva.

**Problemas de regla de negocio** (R09, R10) — no se pueden arreglar
transformando, porque exigen una decisión que el pipeline no puede tomar:

- **R09, el libro descuadrado.** Si `net_amount` no es `amount - fee_amount`,
  ¿cuál de los tres está mal? Liquidar así paga al comercio un importe equivocado.
  **Es dinero real.**
- **R10, capturada sin autorización.** La operación dice estar cobrada y el emisor
  dice que no la aprobó. Una de las dos afirmaciones es falsa, y cobrar sin
  aprobación acaba en contracargo.

Las dos se apartan a `rejected/` para revisión manual.

| Motivo | Filas | Clase |
|--------|-------|-------|
| `DUPLICATE_TXN_ID` | 53 | forma |
| `NULL_AMOUNT` | 50 | forma |
| `LEDGER_MISMATCH` | 24 | **regla de negocio** |
| `CAPTURED_WITHOUT_AUTH` | 16 | **regla de negocio** |

> **R01 pasa por los pelos.** 50 nulos sobre 1000 dan exactamente 0.9500, y el
> umbral es `>= 0.95`. Con un nulo más fallaría. Está calibrado así a propósito:
> un umbral no es una verdad, es una decisión de negocio, y conviene ver lo cerca
> que se está del borde.

**Lo que no se debe leer mal:** que curated pase 10/10 no significa que los datos
estuvieran bien. Significa que 137 filas problemáticas están en `rejected/`,
fuera de curated pero **sin borrarse**. Eso es auditable; un `DELETE` no lo es.

---

## Metadata en el Catálogo

El job agrega metadata al Glue Catalog que va más allá del schema técnico:

| Campo | Ejemplo |
|-------|---------|
| Database description | "Base de datos del catálogo FinCorp — datos curados" |
| `owner` | `data-engineering-team@fincorp.com` |
| `business_domain` | `Finance / Transactions` |
| `source` | `DMS via S3 raw zone (Lab 1)` |
| Table: Quality Score | Visible en el quality_report.json |
| Table: Partitions | `year=2026/month=09/` |

---

## Prerrequisitos

- Lab 1 debe estar deployed con datos en S3 (`./validate.sh` del Lab 1 pasa)
- AWS CLI v2
- `jq` (para mostrar el quality report en color)
- Terraform >= 1.6 y el backend remoto bootstrapeado (ver `infra/README.md`)
- Permisos IAM: Glue, S3, IAM, CloudWatch Logs

---

## Deployment

### Paso 1: Setup de infraestructura (~2 min)

```bash
cd labs/02-transformation-quality-catalog/scripts
./setup.sh
```

El script:
1. Verifica que el Lab 1 existe
2. Sube `glue_job.py` al bucket S3 del Lab 1
3. Aplica el módulo Terraform `infra/lab2` (Glue Job + Crawler + Catalog + DQ Ruleset)

### Paso 2: Ejecutar el pipeline ETL (~3 min)

```bash
./run_job.sh
```

El script:
1. Inicia el Glue job y hace polling (~90s)
2. Descarga y muestra el quality report
3. Inicia el Crawler y espera que complete
4. Muestra las tablas en el Glue Catalog

### Paso 3: Validar

```bash
./validate.sh
```

---

## ✅ Checkpoint — ¿funcionó?

```bash
BUCKET=$(terraform -chdir=../../../infra/lab1 output -raw s3_bucket_name)

# 1. Parquet particionado en curated/
aws s3 ls "s3://$BUCKET/curated/transactions/" --recursive   # ficheros .parquet

# 2. Registros rechazados apartados, NO borrados
aws s3 ls "s3://$BUCKET/rejected/" --recursive               # al menos 1 fichero

# 3. La tabla existe en el catálogo
aws glue get-table --database-name financial_platform \
  --name transactions --query "Table.StorageDescriptor.Columns[].Name"

# 4. El reporte de calidad
aws s3 cp "s3://$BUCKET/quality-reports/latest/quality_report.json" - | jq .
```

**El reporte debe mostrar reglas que fallan.** Eso es correcto: el dataset trae
problemas deliberados. Si todo saliera en verde, la validación no estaría
mirando de verdad.

### Consultarlo como SQL

El Catalog por sí solo no consulta nada: es un índice. **Athena** es lo que lo
convierte en SQL sobre el Parquet, sin mover los datos ni levantar un cluster.
Eso es lo que hace que esto sea un *lakehouse* y no sólo un lago.

El workgroup se crea con el lab, y trae **cuatro consultas ya guardadas**. En la
consola de Athena: *Query editor* → workgroup `fincorp-dev` → pestaña *Saved
queries*.

```sql
-- La vista del negocio del adquirente: quién mueve volumen y qué margen deja.
SELECT
    customer_id AS merchant_id,
    merchant_name,
    COUNT(*)                  AS transacciones,
    ROUND(SUM(amount), 2)     AS volumen_bruto,
    ROUND(SUM(fee_amount), 2) AS comision,
    ROUND(10000.0 * SUM(fee_amount) / NULLIF(SUM(amount), 0), 1) AS mdr_bps
FROM transactions
WHERE status = 'CAPTURED'
GROUP BY customer_id, merchant_name
ORDER BY volumen_bruto DESC
LIMIT 10;
```

Las otras tres: tasa de aprobación por MCC, declinaciones por código ISO 8583, y
una que compara los bytes escaneados con y sin filtro de partición.

> **Esa última es la que conviene ejecutar.** Athena cobra por byte escaneado,
> así que el particionado no es orden: es la factura. Ejecuta las dos variantes
> y mira *Data scanned* en cada una.

El workgroup lleva un `bytes_scanned_cutoff_per_query` de 1 GB. No molesta a este
dataset —son unos pocos MB— pero deja a la vista el freno de mano que en
producción evita que un `SELECT *` sin filtro se lleve por delante el
presupuesto del mes.

```bash
# Los resultados de cada consulta quedan en S3:
terraform -chdir=infra/lab2 output athena_results_path
```

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md#lab-2--transformation--quality--catalog)

---

## Outputs esperados

```bash
# run_job.sh output esperado:
━━━ PASO 2/4: Quality Report ━━━

  Filas procesadas : 1000
  Filas válidas    : ~820
  Filas rechazadas : 137
  Score DQ         : 62.5%
  Reglas pasadas   : 5/8
  Recomendación    : REVIEW: Se recomienda revisar los registros rechazados

  Detalle de reglas:
  ✗ completeness_amount → score: 0.95 (50 filas)
  ✓ completeness_transaction_id → score: 1.0
  ✓ completeness_customer_id → score: 1.0
  ✗ uniqueness_transaction_id → score: 0.97 (30 filas)
  ✓ validity_transaction_type → score: 1.0
  ✓ validity_status → score: 1.0
  ✓ validity_currency → score: 1.0
  ✗ ...
```

El score del dataset curated (solo registros válidos) será más alto: reglas de tipos y status pasarán porque el job normalizó los datos antes de escribir curated.

---

## Cleanup

```bash
./cleanup.sh
```

Elimina: Glue Job/Crawler/Database/Ruleset, IAM Role, datos de `curated/`, `rejected/` y `quality-reports/` en S3 — todo el módulo `infra/lab2`.

**No elimina** el bucket S3 ni los datos `raw/` — esos pertenecen al Lab 1.

Costo del Lab 2 en idle: **$0** (Glue no cobra en reposo). Solo cobra al ejecutar el job: ~$0.0006 por ejecución de 3 minutos.

---

## Conexión con Lab 3

Los datos en `s3://bucket/curated/transactions/` son los "datos confiables" que el Lab 3 puede usar para:
- Ingestar como contexto al vector store (como documentos adicionales)
- Referenciar narrativamente: "la IA opera sobre datos que ya pasaron por este proceso"

El mensaje central: **RAG no comienza en el vector database. Comienza aquí.**

→ **Continúa en:** `labs/03-data-to-ai/README.md`

---

## Casos reales que aplican este patrón

### BMW Group — Data Quality + Catalog a escala

BMW procesa datos de millones de vehículos conectados. Sin validación de calidad, un sensor con valor erróneo puede afectar los modelos de mantenimiento predictivo. La diferencia entre un dato raw y un dato curado es literalmente la diferencia entre una predicción correcta y un error de manufactura.

```
Fuentes raw (vehículos, sensores, ERP)
    │
    ▼ AWS Glue ETL
Transformación + Normalización
    │
    ▼ AWS Glue Data Catalog
Metadata centralizada: schema, owner, clasificación, calidad
    │
    ▼ AWS Lake Formation
Governance: control de acceso por columna y fila
    │
    ▼ Amazon Redshift / SageMaker
Analytics + ML sobre datos confiables
```

**Conexión con este lab:** Glue ETL para transformación + Glue Catalog para metadata es exactamente el stack del Lab 2.

### United Airlines — Catalog Federation

United tiene datos en múltiples plataformas (Redshift, Databricks, S3). Su solución al problema de duplicación de datos: **catalog federation** con AWS Glue Data Catalog.

```
Redshift (DW) ─────────┐
Databricks ────────────┤──► AWS Glue Data Catalog (índice unificado)
S3 (Data Lake) ────────┘         │
                                  ▼
                            Amazon Athena
                        (query sobre cualquier fuente
                          sin mover los datos)
```

El Glue Catalog no es solo un registro de tablas. Puede ser el índice unificado de toda una organización, permitiendo queries federadas sin duplicación. En el Lab 2 vemos la base de esa capacidad: la tabla `transactions` en el Catalog queda disponible para Athena en cuanto el Crawler termina.

**Referencia:** United Airlines, AWS re:Invent 2023 (sesión ANT335)

> *"La diferencia entre 'tengo datos' y 'confío en mis datos' es exactamente lo que construimos en este lab. BMW y United llegaron a esta conclusión a escala de petabytes. Nosotros lo demostramos con 1,000 transacciones de FinCorp."*


---

## Siguiente paso

Los datos ya son confiables y están catalogados. Ahora se pueden usar para algo
que de verdad dependa de ellos: una IA que responda preguntas.

**→ [Lab 3 — Data to AI](../03-data-to-ai/README.md)**

> ¿Paras aquí por hoy? El Lab 2 en reposo cuesta $0, pero el Lab 1 no:
> `bash scripts/cleanup.sh && bash ../01-ingestion-data-lake/scripts/cleanup.sh`

| [← Lab 1](../01-ingestion-data-lake/README.md) | [Índice](../../README.md) | [Troubleshooting](../../docs/troubleshooting.md) |
|---|---|---|
