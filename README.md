# De los Datos a la IA: Construyendo una Plataforma de Datos para IA en AWS

> **AWS Community Day Perú 2026** · Charla técnica de 30 minutos · Nivel: básico-intermedio

---

## La Idea Central

> *"Una aplicación de IA no comienza con el modelo. Comienza con datos que pueden ser ingeridos, transformados, validados, entendidos, gobernados y finalmente entregados a la IA."*

Esta charla es la evolución de **Databases for AI** (AWS Community Day Perú 2025):

```
ANTES (Databases for AI):
  DATABASE → RAG → AI Application

AHORA (Data Platform for AI):
  DATA SOURCES → INGESTION → STORAGE → TRANSFORMATION →
  DATA QUALITY → CATALOG → GOVERNANCE → CURATED DATA →
  ANALYTICS + AI → RAG → AGENTS
```

La arquitectura RAG del repositorio anterior se convierte en la **última etapa** de una plataforma de datos más amplia.

---

## La Historia: FinCorp

FinCorp es una empresa ficticia de servicios financieros con datos transaccionales distribuidos, problemas de calidad de datos y necesidad de un asistente de compliance para su equipo.

A través de tres laboratorios, construimos la plataforma de datos que hace posible ese asistente.

---

## Los Tres Laboratorios

| Lab | Pregunta central | Servicios revelados | Setup |
|-----|-----------------|---------------------|-------|
| [Lab 1 — Ingestion + Data Lake](labs/01-ingestion-data-lake/) | ¿Cómo llevamos datos distribuidos a una plataforma? | AWS DMS + Amazon S3 | ~15 min |
| [Lab 2 — Transformation + Quality + Catalog](labs/02-transformation-quality-catalog/) | ¿Podemos confiar en esos datos? | AWS Glue + Glue Data Quality + Glue Catalog | ~5 min + 3 min job |
| [Lab 3 — Data to AI](labs/03-data-to-ai/) | ¿Cómo usamos esos datos para IA? | Bedrock + Aurora pgvector + DynamoDB + Redis | ~20 min |

Los tres labs forman **una sola historia progresiva**. No son demos independientes.

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

## Estructura del Repositorio

```
aws-ug-databases-for-ia/
│
├── README.md                    ← Este archivo
├── requirements.txt             ← Dependencias Python para desarrollo local
│
├── labs/
│   ├── 01-ingestion-data-lake/
│   │   ├── dataset/             ← transactions.csv, customers.csv, seed.sql
│   │   ├── infrastructure/      ← lab1-cfn.yaml (VPC, RDS, DMS, S3)
│   │   ├── scripts/             ← setup.sh, validate.sh, cleanup.sh
│   │   └── README.md
│   ├── 02-transformation-quality-catalog/
│   │   ├── glue_job.py          ← Glue Python Shell job
│   │   ├── quality_rules.dqdl   ← Ruleset DQDL (8 reglas)
│   │   ├── quality_rules.json   ← Ruleset en JSON (referencia)
│   │   ├── infrastructure/      ← lab2-cfn.yaml (Glue + DQ + Catalog)
│   │   ├── scripts/             ← setup.sh, run_job.sh, validate.sh, cleanup.sh
│   │   └── README.md
│   └── 03-data-to-ai/
│       ├── ingestion/           ← Pipeline de embeddings
│       ├── query/               ← RAG chain + 3 capas de memoria
│       ├── app/                 ← Streamlit UI
│       ├── data/                ← Corpus compliance/onboarding/policies
│       ├── scripts/             ← init_db.py, ingest_documents.py
│       ├── infrastructure/      ← Aurora + Redis + DynamoDB + EC2
│       ├── .env.example         ← Template de variables de entorno
│       ├── requirements.txt     ← Dependencias Lab 3
│       └── README.md
│
├── scripts/
│   └── validate_setup.py        ← Verificación pre-demo de los 3 labs
│
└── .github/workflows/           ← CI/CD para los 3 labs
```

> El código del Lab 3 viene de la charla "Databases for AI" (2025). El
> repositorio de aquella charla sigue accesible íntegro en el tag `v1.0.0`.

---

## Prerrequisitos

- Cuenta AWS con acceso a `us-east-1`
- AWS CLI v2 configurado (`aws configure`)
- Python 3.11+
- `jq` instalado
- Amazon Bedrock habilitado:
  - `amazon.titan-embed-text-v2:0`
  - `amazon.nova-pro-v1:0`

---

## Orden de ejecución para el demo

```bash
# 30 minutos antes del demo (en paralelo si es posible):
cd labs/01-ingestion-data-lake/scripts && ./setup.sh      # ~15 min
cd labs/03-data-to-ai/infrastructure && ./setup.sh       # ~20 min (Lab 3)

# Iniciar DMS task (Lab 1)
aws dms start-replication-task --replication-task-arn $DMS_TASK_ARN \
  --start-replication-task-type start-replication

# Pre-ingestar documentos (Lab 3)
cd labs/03-data-to-ai
python scripts/init_db.py
python scripts/ingest_documents.py --source data/

# Verificar que todo está en pie antes de salir a escena
python scripts/validate_setup.py

# Durante el demo:
cd labs/02-transformation-quality-catalog/scripts && ./setup.sh
./run_job.sh                                             # ~3 min visible
cd labs/03-data-to-ai/infrastructure && ./deploy-app.sh  # Lab 3 app
```

---

## Cleanup — Evitar Costos

Siempre ejecutar al terminar. Costo estimado si se olvida: ~$25-35 USD/día.

```bash
# Lab 2 (Glue — costo $0 en idle, pero limpiar igual)
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh

# Lab 1 (RDS + DMS ~ $0.09/hr)
bash labs/01-ingestion-data-lake/scripts/cleanup.sh

# Lab 3 (Aurora + ElastiCache ~ $15-25/día)
bash labs/03-data-to-ai/infrastructure/cleanup.sh
```

O desde GitHub Actions: `Actions → Cleanup → Run workflow → ELIMINAR`

---

## Evolución desde la charla anterior

Este repositorio es la evolución natural de **Databases for AI** (AWS Community
Day Perú 2025), cuyo código íntegro sigue accesible en el tag [`v1.0.0`](../../tree/v1.0.0).  
El Lab 3 (RAG + 3 capas de memoria) es una continuación directa de esa charla — ahora posicionado como el **consumidor final** de una plataforma de datos completa.

---

## Licencia

Apache-2.0. Ver [LICENSE](LICENSE).
