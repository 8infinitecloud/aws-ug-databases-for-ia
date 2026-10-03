# De los Datos a la IA — Workshop de Plataforma de Datos en AWS

> Construye, paso a paso, la plataforma de datos que hace posible un asistente
> de IA. Tres laboratorios, ~2 horas, menos de $1 en tu cuenta AWS.
>
> Presentado en **AWS Community Day Perú 2026**. El repositorio está pensado
> para seguirlo por tu cuenta, no solo para verlo en la charla.

| | |
|---|---|
| **Duración** | ~2 h 20 en total (Lab 1: 25 min · Lab 2: 15 min · Lab 3: 40 min · Lab 4: 20 min) |
| **Coste** | ~$0.50 si haces cleanup al terminar ([desglose](COST_ESTIMATE.md)) |
| **Nivel** | Básico-intermedio. Necesitas soltura con la terminal y nociones de SQL. |
| **Requisitos** | Cuenta AWS con permisos de admin, AWS CLI v2, Terraform ≥ 1.6 |
| **Región** | `us-east-1` |

---

## La idea central

> *"Una aplicación de IA no comienza con el modelo. Comienza con datos que
> pueden ser ingeridos, transformados, validados, entendidos, gobernados y
> finalmente entregados a la IA."*

La mayoría de los tutoriales de RAG empiezan con documentos ya limpios en una
carpeta. En la vida real los datos están en una base de datos de producción,
vienen sucios y nadie sabe qué significan. **Este workshop empieza ahí.**

```
DATA SOURCES → INGESTION → STORAGE → TRANSFORMATION → DATA QUALITY →
CATALOG → CURATED DATA → ANALYTICS + IA → RAG → AGENTES
   └── Lab 1 ──────────┘   └──────── Lab 2 ────────┘   └─── Lab 3 ───┘
```

---

## Qué vas a construir

Al terminar tendrás corriendo en tu cuenta:

- Un **core de pagos "on-premises"** —PostgreSQL sobre EC2, con una API al estilo
  de Stripe para terminales y tiendas online— cuyos cambios llegan a un **data
  lake en S3** con **AWS DMS y CDC**, segundos después de cada pago
- Un pipeline de **AWS Glue** que aplica esos cambios, une la historia sucia de
  una cartera absorbida, y mide **10 reglas de calidad en tres etapas** —ocho de
  columna y **dos de regla de negocio**—, diciendo **de qué fuente** viene cada
  problema
- Un **catálogo de datos** con esquema y metadata de negocio, consultable con SQL
- Un **asistente de compliance** con RAG sobre Amazon Bedrock, con tres capas de
  memoria (sesión, persistente y semántica), que al final responde con los pagos
  que la sala hizo cinco minutos antes
- **Scoring de fraude en milisegundos** y la **telemetría de los terminales** por
  AWS IoT Core

---

## Empezar

### 1. Comprueba que tienes todo

```bash
git clone <url-del-repo>
cd aws-ug-databases-for-ia

./scripts/preflight.sh
```

Verifica herramientas, credenciales, región y acceso a los modelos de Bedrock.
**No crea nada y no cuesta dinero.** Si algo falta, te dice exactamente qué
hacer.

¿Errores? → [docs/setup.md](docs/setup.md) tiene la instalación detallada por
sistema operativo.

### 2. Crea el backend de Terraform (una sola vez)

```bash
cd infra/bootstrap
terraform init && terraform apply
```

Crea el bucket S3 del estado y la tabla de locks. ~30 segundos.

### 3. Fija la contraseña de las bases de datos

```bash
export TF_VAR_db_password='CambiaEstaPassword2026!'
```

Tenla en la misma terminal durante todo el workshop. Si abres otra, vuelve a
exportarla.

### 4. Levanta el core de pagos

```bash
./scripts/deploy_core.sh                       # infra + imagen del backend (~10 min)
python scripts/provision_demo_keys.py --ssm    # claves de las tiendas online
python scripts/generate_traffic.py --api "$(terraform -chdir=infra/payments output -raw api_endpoint)" --count 50
```

Es el sistema del que salen todos los datos: sin él no hay nada que replicar.
La base arranca sola con su esquema y su cartera de comercios.

### 5. Haz el Lab 1

```bash
cd labs/01-ingestion-data-lake
cat README.md
```

---

## Antes de gastar en AWS: casi todo se prueba en local

No necesitas una cuenta AWS para entender el workshop ni para trabajar en él.

```bash
pip install -r requirements.txt

# El pipeline COMPLETO del Lab 2 contra el dataset real, sin AWS.
# Produce Parquet particionado, el quality report y la zona rejected/.
python scripts/run_pipeline_local.py --keep

# 19 tests del pipeline
pytest tests/ -v

# La infraestructura compila y el grafo resuelve
for M in bootstrap payments lab1 lab2 lab3 lab4; do
  terraform -chdir="infra/$M" init -backend=false && terraform -chdir="infra/$M" validate
done
```

Nada de eso toca AWS ni cuesta dinero. Lo único que sí necesita credenciales
(de solo lectura, y gratis) es `terraform plan`, para ver qué se crearía.

Detalle de qué es local y qué no: [docs/local-development.md](docs/local-development.md).

---

## El recorrido

| Lab | Pregunta que responde | Qué despliegas | Tiempo | Coste |
|-----|----------------------|----------------|--------|-------|
| **[1 — Ingestion + Data Lake](labs/01-ingestion-data-lake/)** | ¿Cómo llevo cada cambio de producción a una plataforma sin tocar el origen? | AWS DMS con CDC desde el PostgreSQL del core, S3 | ~25 min | ~$0.04/h |
| **[2 — Transformation + Quality + Catalog](labs/02-transformation-quality-catalog/)** | ¿Puedo confiar en esos datos? ¿Alguien sabe qué significan? | Glue Job, Glue Data Quality, Glue Catalog, Athena | ~15 min | ~$0.01 total |
| **[3 — Data to AI](labs/03-data-to-ai/)** | ¿Cómo alimento una IA con datos en los que confío? | Bedrock, Aurora pgvector, DynamoDB, Redis, Streamlit | ~40 min | ~$0.12/h |
| **[4 — Streaming + Insight Online](labs/04-streaming-online-insight/)** | ¿Puedo reaccionar *ahora*, no mañana? ¿Por qué falló ese cobro? | IoT Core, Lambda, DynamoDB, Firehose (telemetría) | ~20 min | ~$0.00 en reposo |

**Los cuatro forman una historia progresiva**, y el recorrido tiene una forma:
los tres primeros responden **qué pasó**; el cuarto, **si podemos reaccionar
ahora**. Esa no es la misma pregunta hecha más rápido — es otra arquitectura, y
por eso el Lab 4 guarda en DynamoDB y no en S3.

Son técnicamente independientes: el Lab 3 se puede hacer solo. Las dependencias
reales son que el Lab 2 y el Lab 4 necesitan el bucket del Lab 1.

> Puedes hacerlos en días distintos y destruir cada uno al acabar. Es más
> barato que dejarlos encendidos.

Si quieres que la conexión Lab 2 → Lab 3 sea técnica y no solo narrativa,
`scripts/ingest_from_curated.py` convierte los datos curados en hechos agregados
y los ingesta al vector store. Ver [ADR-013](DECISIONS.md).

---

## La historia: FinCorp

**FinCorp Payments es un adquirente**: procesa pagos con tarjeta y QR para
comercios afiliados. No es el comercio — es quien autoriza, captura, liquida y
cobra la comisión.

Su cartera tiene once comercios —una picantería, una casa de cambio, tiendas
online, un hotel— y uno de ellos es **Smart Coffee**, una cafetería cuyo
terminal físico genera pagos en vivo durante la demo. Su core corre en un
servidor propio que nadie va a migrar este trimestre: la nube entra por los
datos.

FinCorp además **absorbió la cartera de otro adquirente**, cuya historia llega
sucia: importes ausentes, IDs duplicados, códigos antiguos, y dos problemas que
solo existen en pagos — **un libro mayor que no cuadra** y **operaciones cobradas
sin aprobación del emisor**. Nuestro core no tiene esos errores: la base los
impide.

Su equipo de compliance necesita un asistente que responda sobre monitoreo
transaccional y AML de comercios.

El dataset trae **problemas deliberados**. No es un descuido: son lo que hace
que el Lab 2 tenga algo que detectar. Un reporte de calidad todo en verde no
enseñaría nada.

---
## Arquitectura Completa

```
 POS Core2 (QR, tarjeta) · tiendas online · generador de tráfico
                 │ HTTPS
                 ▼
 API Gateway ─► Fargate (API de adquirencia, estilo Stripe)              CORE
                 │                          │ evento de pago
                 ▼                          ▼
 PostgreSQL 16 en EC2 ("on-premises")   AWS IoT Core ◄── telemetría de los terminales
                 │ WAL                      │
                 │ AWS DMS (full load + CDC)│ Lambda ─► DynamoDB   scoring en ms   LAB 4
                 ▼                          │ Firehose ─► raw/telemetry/
 S3 raw/acquiring/<tabla>/  (cada cambio)   │ DynamoDB terminal-state
 S3 raw/legacy/             (la cartera absorbida)                          LAB 1
                 │
                 ▼ AWS Glue (Python Shell): CDC → una fila por cobro → 10 reglas   LAB 2
 S3 curated/transactions/ (source = acquiring | legacy) · rejected/ · quality-reports/
 Glue Data Catalog: transactions, telemetry  ─►  Amazon Athena
                 │
                 ▼ hechos derivados (scripts/ingest_from_curated.py)
 Bedrock (Titan + Nova Pro) · Aurora pgvector · DynamoDB · ElastiCache Redis   LAB 3
 Streamlit — el asistente de compliance
```

---

## Servicios AWS por Capa

| Capa | Servicio | Lab |
|------|----------|-----|
| Core transaccional | PostgreSQL 16 sobre EC2, ECS Fargate, API Gateway | core |
| Ingestion | AWS DMS (full load + CDC) | 1 |
| Storage | Amazon S3 | 1, 2, 3 |
| Transformation | AWS Glue (Python Shell) | 2 |
| Data Quality | AWS Glue Data Quality | 2 |
| Catalog | AWS Glue Data Catalog | 2 |
| Analytics | Amazon Athena | 2 |
| Vector Store | Aurora PostgreSQL + pgvector | 3 |
| Session Memory | Amazon ElastiCache Redis | 3 |
| Persistent Memory | Amazon DynamoDB | 3 |
| LLM + Embeddings | Amazon Bedrock (Nova Pro + Titan) | 3 |
| IoT + streaming | AWS IoT Core, Lambda, DynamoDB, Kinesis Data Firehose | 4 |

---

## Estructura del repositorio

```
aws-ug-databases-for-ia/
│
├── README.md                    ← Empieza aquí
├── COST_ESTIMATE.md             ← Cuánto cuesta y cómo no gastar
├── DECISIONS.md                 ← Por qué cada decisión técnica
│
├── scripts/
│   ├── preflight.sh             ← ¿Tengo todo? (antes de desplegar)
│   ├── validate_setup.py        ← ¿Está todo en pie? (después)
│   ├── deploy_core.sh           ← Levanta el core: infra + imagen del backend
│   ├── provision_demo_keys.py   ← Claves de API de las tiendas de la demo
│   ├── generate_traffic.py      ← Pagos realistas, card testing y telemetría
│   ├── apply_schema.sh          ← Reaplica el esquema en la base viva (SSM)
│   ├── run_pipeline_local.py    ← El Lab 2 completo, sin AWS
│   ├── dms_emulator.py          ← El layout exacto que deja DMS, para probar sin AWS
│   ├── ingest_from_curated.py   ← Conecta el Lab 2 con el Lab 3 de verdad
│   ├── demo_cierre.sh           ← El cierre en vivo: del pago a la respuesta de la IA
│   └── lib/                     ← tf.sh (Terraform) y core.sh (SQL en el core por SSM)
│
├── payments/                    ← El core: esquema, API, web del QR, firmware del POS
│
├── tests/                       ← 180 tests, contra PostgreSQL real y sin AWS
│
├── infra/                       ← TODA la infraestructura (Terraform)
│   ├── README.md                ← Cómo funciona el backend y los módulos
│   ├── bootstrap/               ← Bucket de estado + tabla de locks (1 vez)
│   ├── payments/                ← El core: PostgreSQL en EC2, Fargate, API Gateway
│   ├── lab1/                    ← DMS con CDC, S3 data lake
│   ├── lab2/                    ← Glue Job, Crawler, Catalog, DQ Ruleset, Athena
│   ├── lab3/                    ← Aurora pgvector, Redis, DynamoDB, EC2
│   └── lab4/                    ← IoT Rules, Lambda, DynamoDB, Firehose
│
├── labs/                        ← La guía y el código de cada lab
│   ├── 01-ingestion-data-lake/
│   │   ├── README.md            ← Guía paso a paso
│   │   ├── dataset/             ← transactions.csv, merchants.csv, generate_dataset.py
│   │   └── scripts/             ← setup.sh, validate.sh, cleanup.sh
│   ├── 02-transformation-quality-catalog/
│   │   ├── README.md
│   │   ├── glue_job.py          ← El ETL que corre en Glue
│   │   ├── quality_rules.dqdl   ← Las 10 reglas de calidad
│   │   └── scripts/             ← setup.sh, run_job.sh, validate.sh, cleanup.sh
│   ├── 03-data-to-ai/
│   │   ├── README.md
│   │   ├── ingestion/           ← Pipeline de embeddings
│   │   ├── query/               ← RAG chain + 3 capas de memoria
│   │   ├── app/                 ← UI en Streamlit
│   │   ├── data/                ← Corpus de compliance y operación (11 documentos)
│   │   └── infrastructure/      ← setup.sh, deploy-app.sh, cleanup.sh
│   └── 04-streaming-online-insight/
│       ├── README.md
│       └── risk_scorer.py       ← Las reglas de fraude + el handler de Lambda
│
├── docs/
│   ├── setup.md                 ← Instalación detallada por SO
│   ├── troubleshooting.md       ← Errores reales → causa → arreglo
│   ├── architecture.md          ← Diagramas completos
│   └── github-actions-setup.md  ← Desplegar desde CI
│
└── .github/workflows/           ← CI + deploy + cleanup
```

La infraestructura vive en `infra/` y no dentro de cada lab: así se ve de un
vistazo todo lo que se crea en tu cuenta, y el `terraform destroy` es evidente.

---

## Cleanup — importante

**Hazlo al terminar.** Si lo olvidas son unos $5/día, casi todo de Aurora y
ElastiCache del Lab 3, DMS y el ALB del core, que no escalan a cero. El cleanup
del Lab 1 borra además el slot de replicación del core: si se quedara, acabaría
llenando su disco.

```bash
# En este orden: el Lab 4 y el Lab 2 escriben en el bucket del Lab 1, y el
# cleanup del Lab 1 necesita el core vivo para borrar su slot de replicacion
bash labs/04-streaming-online-insight/scripts/cleanup.sh
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh
bash labs/01-ingestion-data-lake/scripts/cleanup.sh
bash labs/03-data-to-ai/infrastructure/cleanup.sh
terraform -chdir=infra/payments destroy      # el core, al final
```

Comprueba que no queda nada:

```bash
for M in payments lab1 lab2 lab3 lab4; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
# Todos deben dar 0
```

También desde GitHub Actions: `Actions → Cleanup → Run workflow → ELIMINAR`.

> Se quedan a propósito el bucket del estado de Terraform y la tabla de locks:
> cuestan céntimos al mes y son donde vive el estado de todo lo demás.

---

## Documentación

| Documento | Para qué |
|-----------|----------|
| [docs/setup.md](docs/setup.md) | Instalar herramientas y habilitar Bedrock |
| [docs/troubleshooting.md](docs/troubleshooting.md) | Cuando algo falla |
| [COST_ESTIMATE.md](COST_ESTIMATE.md) | Costes por lab y cómo poner un presupuesto |
| [infra/README.md](infra/README.md) | Cómo están organizados los módulos de Terraform |
| [docs/local-development.md](docs/local-development.md) | Qué se puede hacer sin AWS, y cómo |
| [docs/architecture-demo-proposal.md](docs/architecture-demo-proposal.md) | **Propuestas, no implementado.** Smart Coffee: POS físico con M5Stack |
| [docs/datasets.md](docs/datasets.md) | Por qué el dataset es sintético, y alternativas reales (PaySim, NYC TLC, datos abiertos del Perú) |
| [docs/cases.md](docs/cases.md) | AJE, BMW y United: los tres casos reales, con cifras verificadas |
| [docs/frameworks.md](docs/frameworks.md) | Los frameworks de AWS detrás de cada decisión |
| [docs/well-architected.md](docs/well-architected.md) | Qué cumple, qué se concede a propósito, y qué cambiar en producción |
| [docs/architecture.md](docs/architecture.md) | Diagramas de arquitectura completos |
| [DECISIONS.md](DECISIONS.md) | Por qué Glue Python Shell y no PySpark, etc. |
| [docs/backlog.md](docs/backlog.md) | Mejoras posibles, y cuáles merecen la pena antes de la charla |
| [docs/github-actions-setup.md](docs/github-actions-setup.md) | Desplegar desde CI con OIDC |

---

## Origen

Este workshop es la evolución de **Databases for AI** (AWS Community Day Perú
2025), cuyo repositorio sigue accesible íntegro en el tag
[`v1.0.0`](../../tree/v1.0.0). El Lab 3 viene de aquella charla — ahora situado
donde le corresponde: como el **consumidor final** de una plataforma de datos,
no como el punto de partida.

---

## Contribuir

Si encuentras un error, algo que no se entiende o un paso que no funciona en tu
entorno, abre un issue. Ver [CONTRIBUTING.md](CONTRIBUTING.md).

---

## Licencia

Apache-2.0. Ver [LICENSE](LICENSE).
