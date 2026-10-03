# Lab 2 — Transformation + Data Quality + Catalog

> **Lab 2 de 4** · [← Lab 1](../01-ingestion-data-lake/README.md) · [Índice](../../README.md) · [Lab 3 →](../03-data-to-ai/README.md)

| | |
|---|---|
| **Tiempo** | ~15 min (el job tarda ~30 s; el crawler, ~2 min) |
| **Coste** | ~$0.01 en total · **$0 en reposo** (Glue solo cobra cuando corre) |
| **Qué despliegas** | Glue Job, Glue Crawler, Glue Data Catalog, DQ Ruleset |
| **Requisitos** | **El Lab 1 desplegado** — este lab lee su bucket |

**Objetivo:** convertir lo que llegó a `raw/` —las tablas del core, cambio a
cambio, y el volcado sucio de la cartera legacy— en Parquet confiable y
catalogado, apartando los registros que no pasan las reglas de calidad.

**Qué te llevas:** que "calidad de datos" no es limpiar y callar, sino medir,
documentar y **conservar lo que se rechazó**. Y que un catálogo es lo que hace
que un fichero en S3 sea un dato con significado.

---

## El Problema

Después del Lab 1, en `raw/` hay dos cosas muy distintas:

| Fuente | Qué llega | Cómo está |
|---|---|---|
| `raw/acquiring/` | Las tablas del core, una carpeta por tabla, con **varias versiones de cada fila** (I, U, D) | Limpias: la base impide los errores con constraints. Pero sueltas: no hay una fila por cobro |
| `raw/legacy/` | El volcado de la cartera absorbida | **Sucio**: importes nulos, duplicados, fechas DD/MM/YYYY, códigos antiguos (`TARJETA`, `CAPTURADA`), libros descuadrados |

Tener datos no es lo mismo que poder confiar en ellos. **¿Puedes alimentar una IA
con esto? Técnicamente, sí. ¿Deberías? No.**

El Lab 2 responde: *"¿Podemos confiar en estos datos, y de dónde viene lo que no
cuadra?"*

```
ANTES                                          DESPUÉS
raw/acquiring/payment_intents/                 curated/transactions/
  I  301  REQUIRES_CONFIRMATION                  una fila por cobro, estado final
  U  301  SUCCEEDED                              source = acquiring | legacy
raw/legacy/  "CAPTURADA", "15/03/2025", NULL   rejected/ con su motivo
```

---

## La Decisión de Arquitectura

### ¿Por qué Glue Python Shell (no PySpark)?

Para unos miles de filas, PySpark sería overkill. Python Shell inicia en ~20 segundos vs 3 minutos de Spark, y el código es pandas — la librería que todos conocen.

Glue cobra por **DPU-hora** (1 DPU = 4 vCPU + 16 GB, ~$0.44/h, facturado por
segundo). El job usa **0.0625 DPU** (`max_capacity` en `infra/lab2/glue.tf`):
una ejecución de 30 s cuesta ~$0.0002. Un job de Spark arranca con un mínimo de
2 DPU.

En producción con millones de filas, migrar a PySpark Glue Job cambia solo el `command.name = "glueetl"` en `infra/lab2/glue.tf` y los DataFrames de pandas a Spark. La lógica de negocio queda igual.

### ¿Por qué Glue Data Quality con DQDL?

DQDL (Data Quality Definition Language) es declarativo y legible:
```
Completeness "amount" >= 0.95
Uniqueness "transaction_id" = 1.0
ColumnValues "payment_method" in ["CARD_PRESENT", "QR", "CARD_NOT_PRESENT"]
```

Cualquiera puede leer estas reglas y entender qué se está validando. No necesitas saber programar para definir expectativas sobre datos.

### ¿Por qué Glue Data Catalog?

El Crawler auto-descubre el schema técnico (columnas, tipos, particiones). Pero el Catalog también guarda metadata de negocio: quién es el owner, de dónde vienen los datos, cuál es el quality score. La diferencia entre "tengo una tabla" y "entiendo mis datos".

---

## Arquitectura

```
S3 raw/acquiring/<tabla>/  ← el core, por DMS con CDC (Parquet)
S3 raw/legacy/             ← la cartera absorbida (CSV)
    │
    ▼
┌─────────────────────────────────────────────┐
│   AWS Glue Job (Python Shell)               │
│                                             │
│   [1] Lee las dos fuentes                   │
│       · aplica el CDC: última versión de    │
│         cada fila, sin las borradas         │
│       · JOIN: una fila por cobro            │
│       · las une con la columna `source`     │
│   [2] Normaliza fechas, canales, estados    │
│   [3] Evalúa las 10 reglas de calidad       │
│   [4] Separa válidos vs rechazados          │
│   [5] Escribe Parquet particionado          │
│   [6] quality_report.json, por fuente       │
└──────────┬──────────────┬───────────────────┘
           ▼              ▼
    S3 curated/     S3 rejected/
    (Parquet)       (CSV con razón)
           │
           ▼
┌─────────────────────────────────────┐
│   Glue Crawler                      │
│   curated/transactions/ y           │
│   raw/telemetry/ (Lab 4)            │
└──────────┬──────────────────────────┘
           ▼
┌─────────────────────────────────────┐
│   Glue Data Catalog                 │
│   financial_platform.transactions   │
│   financial_platform.telemetry      │
└─────────────────────────────────────┘
           │
           ▼
    Amazon Athena            Lab 3 (RAG)
    (SQL sobre S3)           (hechos derivados)
```

### El JOIN que antes hacía la base

DMS no transforma: entrega `charges`, `merchants`, `terminals` y
`balance_transactions` por separado. El job hace el mismo JOIN que la vista
`v_transactions_for_ingestion` del core, y un test lo compara **fila a fila** con
la vista después de aplicar el CDC (`tests/test_dms_glue.py`): mismos cobros,
mismos importes, comisiones y códigos. Si alguien cambia uno, el otro avisa.

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
**La cartera legacy: 1000 operaciones → 863 válidas / 137 apartadas.**

### De dónde viene cada problema

El reporte desglosa la calidad **por fuente** (`by_source`), y la historia sale
sola:

| Fuente | Reglas que fallan en origen | Rechazados |
|---|---|---|
| `acquiring` (el core) | **ninguna** | **0** |
| `legacy` (la cartera absorbida) | 5 | 137 |

El core no aporta rechazados porque la base impide esos errores con constraints.
Si alguna vez aportara uno, no sería un problema de datos: sería un fallo del
JOIN o del backend, y `validate.sh` lo marca como error.

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
| `source` | `DMS via S3 raw zone (Lab 1)` (la tabla lleva además una columna `source` por fila) |
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
1. Inicia el Glue job y hace polling (~30 s)
2. Descarga y muestra el quality report
3. Inicia el Crawler y espera que complete (~2 min)
4. Muestra las tablas en el Glue Catalog

**El crawler no se ejecuta solo.** No tiene horario ni evento: lo lanza
`run_job.sh`. No mueve datos: actualiza el esquema y las particiones en el
catálogo. Un pago nuevo que cae en una partición existente (`year=2026/month=10`)
se ve en Athena sin volver a ejecutarlo; hace falta cuando aparece una partición
nueva (un mes nuevo), cambia el esquema o llega la primera telemetría del Lab 4.

Para incorporar pagos nuevos a `curated/`, vuelve a ejecutar `./run_job.sh`.

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

Resultado esperado del reporte: las reglas **fallan en `raw`** y pasan en
`curated` (10/10), con ~137 filas rechazadas, todas de `source = legacy`, y 0 del
core. El dataset trae problemas deliberados: que fallen en origen es lo correcto.

### Consultarlo como SQL

El Catalog por sí solo no consulta nada: es un índice. **Athena** es lo que lo
convierte en SQL sobre el Parquet, sin mover los datos ni levantar un cluster.
Eso es lo que hace que esto sea un *lakehouse* y no sólo un lago.

El workgroup se crea con el lab, y trae **ocho consultas ya guardadas**. En la
consola de Athena: *Query editor* → workgroup `fincorp-datalake-lab` → pestaña
*Saved queries*.

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

Las demás:

| # | Consulta | Qué enseña |
|---|---|---|
| 2 | Tasa de aprobación por MCC | El MCC como señal de riesgo |
| 3 | Declinaciones por código ISO 8583 | `05`, `51`, `54`: qué dice el emisor |
| 4 | Por qué particionamos | Bytes escaneados con y sin filtro de partición |
| 5 | El core frente a la cartera legacy | Lo que aporta cada `source` |
| 6 | **¿Falló la red o el emisor?** | Cruza la telemetría de cada terminal con sus cobros |
| 7 | Abandono del QR por terminal | El embudo que la base no ve: cancelados frente a expirados |
| 8 | Terminales que dejaron de latir | Último latido y batería |

Las 6 a 8 necesitan el Lab 4 (la tabla `telemetry`): sin ella fallan con
`TABLE_NOT_FOUND`.

**La consulta 4, paso a paso.** Athena cobra por byte escaneado:

1. Ejecútala tal cual y anota *Data scanned* (~11 KB: lee todas las particiones).
2. Quita el `--` de la línea `WHERE year = '2024' AND month = '03'` y ejecútala
   otra vez (~0,2 KB: lee un solo directorio).

Athena ejecuta una sola sentencia por vez: no añadas un `;` seguido de otra
consulta o de un comentario.

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

Con el core y el legacy (los números del core dependen de cuántos pagos se
hayan hecho):

```
Calidad por etapa
  regla                               raw   transf  curated   resuelto por
  completeness_amount              PASS   PASS   PASS   -
  uniqueness_transaction_id        FAIL   FAIL   PASS   rechazo
  validity_payment_method          FAIL   PASS   PASS   transformacion
  validity_status                  FAIL   PASS   PASS   transformacion
  ledger_coherence                 FAIL   FAIL   PASS   rechazo
  captured_implies_approved        FAIL   FAIL   PASS   rechazo
  ...

Por fuente
  acquiring    150 validas /    0 rechazadas   fallaban en origen: ninguna
  legacy       863 validas /  137 rechazadas   fallaban en origen: captured_implies_approved, ...
```

Para verlo sin AWS, con el layout exacto que deja DMS:

```bash
python scripts/run_pipeline_local.py
```

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

`curated/transactions/` es lo que el Lab 3 convierte en hechos citables:
`scripts/demo_cierre.sh` ejecuta este job, genera los hechos agregados y los
ingesta en el vector store (ver el
[paso 5 del Lab 3](../03-data-to-ai/README.md#paso-5-del-pago-a-la-respuesta)).

→ **Continúa en:** [Lab 3 — Data to AI](../03-data-to-ai/README.md)

---

## Siguiente paso

Los datos ya son confiables y están catalogados. Ahora se pueden usar para algo
que de verdad dependa de ellos: una IA que responda preguntas.

**→ [Lab 3 — Data to AI](../03-data-to-ai/README.md)**

> ¿Paras aquí por hoy? El Lab 2 en reposo cuesta $0, pero el Lab 1 no:
> `bash scripts/cleanup.sh && bash ../01-ingestion-data-lake/scripts/cleanup.sh`

| [← Lab 1](../01-ingestion-data-lake/README.md) | [Índice](../../README.md) | [Troubleshooting](../../docs/troubleshooting.md) |
|---|---|---|
