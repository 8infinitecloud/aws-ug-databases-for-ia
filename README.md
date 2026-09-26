# De los Datos a la IA — Workshop de Plataforma de Datos en AWS

> Construye, paso a paso, la plataforma de datos que hace posible un asistente
> de IA. Tres laboratorios, ~2 horas, menos de $1 en tu cuenta AWS.
>
> Presentado en **AWS Community Day Perú 2026**. El repositorio está pensado
> para seguirlo por tu cuenta, no solo para verlo en la charla.

| | |
|---|---|
| **Duración** | ~2 h en total (Lab 1: 25 min · Lab 2: 15 min · Lab 3: 40 min) |
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

- Una base de datos MySQL replicándose a un **data lake en S3** con AWS DMS
- Un pipeline de **AWS Glue** que transforma esos datos, les aplica **8 reglas
  de calidad**, aparta los registros malos y escribe Parquet particionado
- Un **catálogo de datos** con esquema y metadata de negocio, consultable con SQL
- Un **asistente de compliance** con RAG sobre Amazon Bedrock, con tres capas de
  memoria (sesión, persistente y semántica)

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

### 4. Haz el Lab 1

```bash
cd labs/01-ingestion-data-lake
cat README.md
```

---

## El recorrido

| Lab | Pregunta que responde | Qué despliegas | Tiempo | Coste |
|-----|----------------------|----------------|--------|-------|
| **[1 — Ingestion + Data Lake](labs/01-ingestion-data-lake/)** | ¿Cómo llevo datos de producción a una plataforma sin tocar el origen? | RDS MySQL, AWS DMS, S3 | ~25 min | ~$0.09/h |
| **[2 — Transformation + Quality + Catalog](labs/02-transformation-quality-catalog/)** | ¿Puedo confiar en esos datos? ¿Alguien sabe qué significan? | Glue Job, Glue Data Quality, Glue Catalog | ~15 min | ~$0.01 total |
| **[3 — Data to AI](labs/03-data-to-ai/)** | ¿Cómo alimento una IA con datos en los que confío? | Bedrock, Aurora pgvector, DynamoDB, Redis, Streamlit | ~40 min | ~$0.12/h |

**Los tres forman una historia progresiva**, pero son técnicamente
independientes: el Lab 3 se puede hacer solo. La única dependencia real es que
el Lab 2 necesita el Lab 1 desplegado, porque lee su bucket.

> Puedes hacerlos en días distintos y destruir cada uno al acabar. Es más
> barato que dejarlos encendidos.

---

## La historia: FinCorp

FinCorp es una empresa ficticia de servicios financieros. Tiene sus
transacciones en un MySQL de producción, los datos vienen con problemas reales
(importes nulos, IDs duplicados, estados en minúsculas, tipos inválidos), y su
equipo de compliance necesita un asistente que responda preguntas sobre
políticas internas.

El dataset trae **problemas deliberados**. No es un descuido: son lo que hace
que el Lab 2 tenga algo que detectar. Un reporte de calidad todo en verde no
enseñaría nada.

---
## Arquitectura Completa

```
┌──────────────────────────────────────────────────────────────────────────┐
│  DATA SOURCES                                                             │
│  RDS MySQL (transacciones FinCorp)                                       │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │ AWS DMS (Full Load + CDC)           LAB 1
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  RAW DATA — Amazon S3                                                     │
│  s3://fincorp-datalake-{account}/raw/transactions/                       │
│  s3://fincorp-datalake-{account}/raw/customers/                          │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │ AWS Glue (Python Shell)              LAB 2
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  TRANSFORMATION + DATA QUALITY + CATALOG                                  │
│  Glue Data Quality → 8 reglas DQDL → ~180 registros rechazados           │
│  Glue Data Catalog → financial_platform.transactions_curated             │
└────────────────────────┬─────────────────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────────────────┐
│  CURATED DATA — Amazon S3                                                 │
│  s3://bucket/curated/transactions/year=2026/month=09/                    │
└─────────────┬──────────────────────────────────────────────────────────┘
              │                         │
              ▼                         ▼
┌─────────────────────┐     ┌──────────────────────────────────────────────┐
│  ANALYTICS          │     │  AI / GenAI                    LAB 3          │
│  Amazon Athena      │     │  Bedrock (Titan Embeddings + Nova Pro)        │
│  (SQL sobre S3)     │     │  Aurora pgvector (vector store)               │
└─────────────────────┘     │  DynamoDB (memoria persistente)               │
                             │  ElastiCache Redis (memoria de sesión)        │
                             │  Streamlit (Financial Compliance Assistant)   │
                             └──────────────────────────────────────────────┘
```

---

## Servicios AWS por Capa

| Capa | Servicio | Lab |
|------|----------|-----|
| Ingestion | AWS DMS | 1 |
| Storage | Amazon S3 | 1, 2, 3 |
| Transformation | AWS Glue (Python Shell) | 2 |
| Data Quality | AWS Glue Data Quality | 2 |
| Catalog | AWS Glue Data Catalog | 2 |
| Analytics | Amazon Athena | 2 (mención) |
| Vector Store | Aurora PostgreSQL + pgvector | 3 |
| Session Memory | Amazon ElastiCache Redis | 3 |
| Persistent Memory | Amazon DynamoDB | 3 |
| LLM + Embeddings | Amazon Bedrock (Nova Pro + Titan) | 3 |

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
│   └── lib/tf.sh                ← Helpers de Terraform para los scripts
│
├── infra/                       ← TODA la infraestructura (Terraform)
│   ├── README.md                ← Cómo funciona el backend y los módulos
│   ├── bootstrap/               ← Bucket de estado + tabla de locks (1 vez)
│   ├── lab1/                    ← VPC, RDS MySQL, DMS, S3 data lake
│   ├── lab2/                    ← Glue Job, Crawler, Catalog, DQ Ruleset
│   └── lab3/                    ← Aurora pgvector, Redis, DynamoDB, EC2
│
├── labs/                        ← La guía y el código de cada lab
│   ├── 01-ingestion-data-lake/
│   │   ├── README.md            ← Guía paso a paso
│   │   ├── dataset/             ← transactions.csv, customers.csv, seed.sql
│   │   └── scripts/             ← setup.sh, validate.sh, cleanup.sh
│   ├── 02-transformation-quality-catalog/
│   │   ├── README.md
│   │   ├── glue_job.py          ← El ETL que corre en Glue
│   │   ├── quality_rules.dqdl   ← Las 8 reglas de calidad
│   │   └── scripts/             ← setup.sh, run_job.sh, validate.sh, cleanup.sh
│   └── 03-data-to-ai/
│       ├── README.md
│       ├── ingestion/           ← Pipeline de embeddings
│       ├── query/               ← RAG chain + 3 capas de memoria
│       ├── app/                 ← UI en Streamlit
│       ├── data/                ← Corpus de compliance (7 documentos)
│       └── infrastructure/      ← setup.sh, deploy-app.sh, cleanup.sh
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

**Hazlo al terminar.** Si lo olvidas son ~$5/día, casi todo de Aurora, DMS y
ElastiCache, que no escalan a cero.

```bash
# En este orden: el Lab 2 escribe en el bucket que crea el Lab 1
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh
bash labs/01-ingestion-data-lake/scripts/cleanup.sh
bash labs/03-data-to-ai/infrastructure/cleanup.sh
```

Comprueba que no queda nada:

```bash
for M in lab1 lab2 lab3; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
# Los tres deben dar 0
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
| [docs/architecture.md](docs/architecture.md) | Diagramas de arquitectura completos |
| [DECISIONS.md](DECISIONS.md) | Por qué Glue Python Shell y no PySpark, etc. |
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
