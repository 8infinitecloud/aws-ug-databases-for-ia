# Lab 1 — Ingestion + Data Lake

> **Lab 1 de 4** · [← Índice del workshop](../../README.md)

| | |
|---|---|
| **Tiempo** | ~25 min (de los cuales ~10 son espera de AWS) |
| **Coste** | ~$0.04/hora mientras la replicación está activa (`dms.t3.small`) |
| **Qué despliegas** | El bucket del data lake y AWS DMS con CDC desde el core |
| **Requisitos** | `infra/payments` desplegado (el core), `./scripts/preflight.sh` en verde |

**Objetivo:** llevar cada cambio de la base de producción al data lake **mientras
ocurre**, sin escribir un ETL y sin tocar la base.

**Qué te llevas:** por qué la ingesta es un problema aparte del análisis, por qué
el **CDC** ve lo que una consulta no ve, y por qué un data lake tiene zonas.

---

## El problema

El core de FinCorp —el sistema que autoriza y captura los pagos— corre en un
servidor propio: un PostgreSQL que nadie va a migrar este trimestre. Es lo que
tiene la mayoría de bancos y adquirentes de la región. Analytics necesita esos
datos, y el equipo de IA también, **sin cargar la base de producción**.

Y no basta con una foto diaria. En pagos, **el cambio de estado es el dato**: un
cobro se crea, se captura, se devuelve, recibe un contracargo. Cada uno es una
actualización de una fila que ya existía.

```
   Core "on-premises"                         AWS
┌──────────────────────┐            ┌──────────────────────────────────┐
│ PostgreSQL 16 (EC2)  │   WAL      │ AWS DMS                          │
│ payments, charges,   │ ─────────▶ │ full load  +  CDC                │
│ merchants, ledger... │  (CDC)     │                                  │
└──────────────────────┘            └───────────────┬──────────────────┘
                                                    ▼
                                    s3://…/raw/acquiring/<tabla>/
                                      LOAD00000001.parquet      la foto inicial
                                      2026/10/15/*.parquet      cada cambio, ~30 s
```

---

## La decisión: DMS con CDC

DMS **no transforma: replica**. Lee el log de la base (el WAL), así que no lanza
consultas contra las tablas, y entrega cada cambio como una fila con su
operación:

```
Op  dms_timestamp        id   intent_reference  status
I   2026-10-15 19:02:11  301  PI5C1A...         REQUIRES_CONFIRMATION
U   2026-10-15 19:02:43  301  PI5C1A...         SUCCEEDED        ← alguien pagó el QR
```

| | CDC por log (DMS) | Incremental por consulta (Glue JDBC) |
|---|---|---|
| El cobro que pasa de pendiente a capturado | **Lo ve** (U) | No, salvo un `updated_at` fiable en todas las tablas |
| Un borrado | Llega como `D` | Invisible |
| Carga sobre producción | Lee el WAL | Un `SELECT` en cada ejecución |
| Latencia | ~30 s | La del job programado |

Sobre un PostgreSQL autogestionado no hay *zero-ETL*: las opciones reales son DMS
o Debezium + Kafka. Ver [ADR-024](../../DECISIONS.md).

### Lo que se replica, y lo que no

Se replican las tablas del negocio del adquirente: `merchants`, `terminals`,
`payment_intents`, `charges`, `balance_transactions`, `refunds`, `disputes`,
`payouts` y `webhook_events`.

**No** se replican, porque un data lake es un sitio donde mucha gente lee: las
claves de API (hashes de credenciales), el secreto de firma de los webhooks, los
tokens de pago (alcance PCI) ni las tablas puramente operativas. Y la cuenta de
liquidación del comercio se quita con una regla `remove-column`.

### La cartera legacy

FinCorp absorbió la cartera de otro adquirente, cuyo sistema no tiene DMS. Su
historia —1000 operaciones, **sucia** a propósito— llega como un volcado a
`raw/legacy/`. El Lab 2 la une a la del core. Ver
[docs/datasets.md](../../docs/datasets.md).

### El data lake

```
s3://fincorp-datalake-<cuenta>/
├── raw/                      lo que llegó, sin tocar
│   ├── acquiring/<tabla>/    el core, por DMS (Parquet)
│   ├── legacy/               la cartera absorbida (CSV)
│   └── telemetry/            los terminales, por Firehose (Lab 4)
├── curated/                  lo que hizo el Lab 2 con ello
├── rejected/                 lo que no pasó las reglas, con su motivo
└── quality-reports/
```

---

## El riesgo que hay que conocer: el slot de replicación

Para el CDC, DMS crea un *replication slot* en PostgreSQL, y la base **retiene el
WAL hasta que DMS lo consume**. Si la tarea se para y nadie borra el slot, el WAL
crece hasta llenar el disco **y la base de producción cae**. Es el fallo clásico
del CDC sobre PostgreSQL.

Las defensas, ya puestas:

1. `max_slot_wal_keep_size = 4GB` en el core: pasado el tope, PostgreSQL invalida
   el slot en vez de llenar el disco. Mejor perder el CDC que el core.
2. Alarmas sobre el WAL retenido y el disco (`FinCorp/Postgres` en CloudWatch).
3. `cleanup.sh` **borra el slot** después de destruir DMS. DMS no lo hace.

---

## Deployment

```bash
cd labs/01-ingestion-data-lake/scripts
./setup.sh
```

1. Despliega el bucket y DMS en la VPC del core (~10 min: la instancia de DMS).
2. Sube la cartera legacy a `raw/legacy/`.
3. **Arranca la tarea** y espera a que la foto inicial esté al 100 %. Desde ese
   momento el CDC escucha.

Si la tarea estaba parada (por ejemplo, entre ensayos), `setup.sh` la **retoma**
donde lo dejó en vez de recargar.

---

## ✅ Checkpoint — ¿funcionó?

```bash
./validate.sh
```

Comprueba que la tarea está en `running`, que cada tabla tiene su foto inicial,
que el slot del core está activo y que el legacy está en su sitio. Y la prueba
que importa: **hace un pago de verdad por la API y espera a verlo llegar a `raw/`
como un delta**. Que exista la foto inicial solo dice que DMS sabe leer; esto
dice que escucha.

Para verlo tú:

```bash
python ../../../scripts/generate_traffic.py \
  --api "$(terraform -chdir=../../../infra/payments output -raw api_endpoint)" --count 20

BUCKET=$(terraform -chdir=../../../infra/lab1 output -raw s3_bucket_name)
aws s3 ls "s3://$BUCKET/raw/acquiring/payment_intents/" --recursive
```

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md#lab-1--ingestion--data-lake)

---

## Cleanup

```bash
./cleanup.sh
```

Para la tarea, vacía y destruye el módulo (DMS, S3, IAM) y **borra el slot de
replicación del core**. No toca el core: para eso, `infra/payments`.

**Coste si se olvida:** ~$0.04/hora (~$1/día) por la instancia de DMS, que con
CDC está siempre encendida. Y el slot, si no se borrara, acabaría llenando el
disco del core.

---

## Conexión con el Lab 2

```
raw/acquiring/<tabla>/  ─┐
                         ├──▶ Glue: aplica los cambios, une, valida ──▶ curated/
raw/legacy/             ─┘                                         └──▶ rejected/
```

Glue se queda con la última versión de cada fila, reconstruye una fila por cobro
y la une a la cartera legacy con una columna `source`. El core no debería aportar
ningún rechazado: la base impide esos errores. La deuda viene del legacy.

→ **Continúa en:** [Lab 2 — Transformation + Quality + Catalog](../02-transformation-quality-catalog/README.md)

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

**El punto:** AJE no empezó con IA. Empezó con el problema básico que estamos resolviendo en este lab — llevar datos distribuidos a un lugar centralizado y confiable. Y con DMS con CDC desde sistemas que no iba a migrar, como nuestro core.

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

Ya tienes en el data lake el core, cambio a cambio, y la cartera legacy. Son
tablas sueltas y un volcado sucio — eso es exactamente lo que resuelve el Lab 2.

**→ [Lab 2 — Transformation + Quality + Catalog](../02-transformation-quality-catalog/README.md)**

> ¿Paras aquí por hoy? Haz cleanup para no pagar de más:
> `./cleanup.sh` (borra también el slot de replicación del core)

| [← Índice](../../README.md) | [Costes](../../COST_ESTIMATE.md) | [Troubleshooting](../../docs/troubleshooting.md) |
|---|---|---|
