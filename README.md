# Financial Compliance Assistant — RAG + Memory Lab

> **AWS Community Day Lab** | Embeddings, RAG y Memoria en Sistemas con LLM  
> Stack: Amazon Bedrock · Titan Embeddings · Aurora pgvector · DynamoDB · ElastiCache Redis · LangChain · Streamlit

## Caso de Estudio

Una empresa de servicios financieros necesita un asistente inteligente que responda preguntas sobre sus procesos internos de **compliance**, **onboarding de clientes** y **políticas internas**. El asistente debe recordar preferencias del usuario entre sesiones y aprender de conversaciones pasadas.

Este laboratorio muestra visualmente las tres capas de memoria y el pipeline RAG completo en tiempo real.

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         USUARIO (Streamlit UI)                          │
└──────────────────────────────────┬──────────────────────────────────────┘
                                   │ pregunta
                    ┌──────────────▼──────────────┐
                    │      LangChain RAG Chain     │
                    │  (query/chain.py)            │
                    └──┬──────────┬──────────┬────┘
                       │          │          │
          ┌────────────▼──┐  ┌────▼─────┐  ┌▼───────────────┐
          │  CAPA 1:      │  │  CAPA 2: │  │  CAPA 3:       │
          │  Memoria de   │  │  Memoria │  │  Memoria       │
          │  Sesión       │  │  Usuario │  │  Semántica     │
          │               │  │          │  │                │
          │ ElastiCache   │  │ DynamoDB │  │ Aurora         │
          │ Redis         │  │          │  │ pgvector       │
          │ (últimos N    │  │(prefs,   │  │(embeddings de  │
          │  mensajes)    │  │ historial│  │ conversaciones │
          │               │  │ resumen) │  │ pasadas)       │
          └───────────────┘  └──────────┘  └────────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │     VECTOR STORE (RAG)       │
                    │   Aurora PostgreSQL           │
                    │   + pgvector extension       │
                    │                              │
                    │  Similarity Search →         │
                    │  Top-K chunks + scores       │
                    └──────────────┬───────────────┘
                                   │
                    ┌──────────────▼──────────────┐
                    │      AMAZON BEDROCK          │
                    │                             │
                    │  Titan Embeddings V2        │
                    │  (embed query + docs)       │
                    │                             │
                    │  Claude Sonnet 3.5          │
                    │  (generación final)         │
                    └─────────────────────────────┘
```

### Flujo de Ingesta (una sola vez, pre-demo)

```
S3 (PDFs/Markdown)
    │
    ▼
DocumentLoader (LangChain)
    │
    ▼
RecursiveCharacterTextSplitter
  chunk_size=1000, overlap=200
    │
    ▼
Amazon Titan Embeddings V2
  (1024 dimensiones)
    │
    ▼
Aurora pgvector
  (tabla: document_chunks)
```

---

## Servicios AWS Utilizados

| Servicio | Rol | Documentación |
|----------|-----|---------------|
| [Amazon Bedrock](https://docs.aws.amazon.com/bedrock/) | LLM (Claude) + Embeddings (Titan) | [Bedrock Docs](https://docs.aws.amazon.com/bedrock/latest/userguide/) |
| [Aurora PostgreSQL Serverless v2](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/aurora-serverless-v2.html) | Vector store con pgvector | [pgvector extension](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/PostgreSQL_pg_vector.html) |
| [Amazon ElastiCache for Redis](https://docs.aws.amazon.com/elasticache/) | Memoria de sesión (TTL corto) | [ElastiCache Docs](https://docs.aws.amazon.com/elasticache/latest/red-ug/) |
| [Amazon DynamoDB](https://docs.aws.amazon.com/dynamodb/) | Memoria persistente de usuario | [DynamoDB Docs](https://docs.aws.amazon.com/amazondynamodb/latest/developerguide/) |
| [Amazon S3](https://docs.aws.amazon.com/s3/) | Almacenamiento de documentos fuente | [S3 Docs](https://docs.aws.amazon.com/AmazonS3/latest/userguide/) |
| [AWS IAM](https://docs.aws.amazon.com/iam/) | Roles y permisos | [IAM Docs](https://docs.aws.amazon.com/IAM/latest/UserGuide/) |

---

## Prerrequisitos

- Cuenta AWS con acceso a `us-east-1` (o `us-west-2`)
- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html) configurado (`aws configure`)
- Python 3.11+
- Acceso habilitado a modelos en Amazon Bedrock:
  - `amazon.titan-embed-text-v2:0`
  - `anthropic.claude-3-5-sonnet-20241022-v2:0`
- Permisos IAM: `AmazonBedrockFullAccess`, `AmazonDynamoDBFullAccess`, `AmazonElastiCacheFullAccess`, `AmazonRDSFullAccess`, `AmazonS3FullAccess`

### Habilitar modelos en Bedrock

```bash
# Verificar acceso a modelos (us-east-1)
aws bedrock list-foundation-models \
  --by-provider amazon \
  --query "modelSummaries[?modelId=='amazon.titan-embed-text-v2:0']" \
  --region us-east-1
```

Si no tienes acceso, ve a **Amazon Bedrock Console → Model access → Request access**.

---

## Deployment

Hay dos formas de desplegar el lab: **scripts locales** (recomendado para la primera vez) o **GitHub Actions** (CI/CD automatizado).

---

### Opción A — Scripts locales

#### Paso 1: Clonar el repositorio

```bash
git clone https://github.com/aws-samples/financial-compliance-rag-lab
cd financial-compliance-rag-lab
```

#### Paso 2: Crear el stack de infraestructura (~20 min)

```bash
cd infrastructure
chmod +x setup.sh
./setup.sh
```

El script crea: VPC, Aurora PostgreSQL Serverless v2 con `pgvector`, ElastiCache Redis, DynamoDB, S3, EC2 App Server y Secrets Manager.

> **Nota para el lab:** Ejecuta este paso 20 minutos antes de la demo en vivo.

#### Paso 3: Desplegar la aplicación (~5 min)

```bash
chmod +x deploy-app.sh
./deploy-app.sh

# Para re-ingestar documentos desde cero:
./deploy-app.sh --reingest
```

El script empaqueta el código, lo sube a S3 y lo ejecuta en el EC2 vía SSM Run Command. Al terminar, imprime la URL de Streamlit.

#### Paso 4 (opcional): Desarrollo local

Para iterar en el código localmente sin desplegar en EC2:

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # completar con outputs del stack
python scripts/init_db.py
python scripts/ingest_documents.py --source data/
streamlit run app/app.py    # http://localhost:8501
```

---

### Opción B — GitHub Actions (CI/CD)

Prerequisito: seguir los pasos de [`docs/github-actions-setup.md`](docs/github-actions-setup.md) para crear el OIDC Provider y el IAM Role en AWS. Solo se hace una vez por cuenta.

#### Workflows disponibles

| Workflow | Trigger | Qué hace |
|----------|---------|----------|
| **CI — Validate** | Push / PR a `main` | Python lint, shellcheck, cfn-lint, verifica corpus |
| **Deploy Infrastructure** | Manual | CloudFormation deploy → dispara Deploy App al terminar |
| **Deploy App** | Push a `main` (cambios en `app/`, `data/`, etc.) | Empaqueta código → S3 → SSM Run Command en EC2 |
| **Cleanup** | Manual (requiere escribir `ELIMINAR`) | Vacía S3, elimina DynamoDB y el stack completo |

#### Flujo completo

```
PR abierto
    └─► CI — Validate (automático)

Push a main (cambios en app/ o data/)
    └─► Deploy App (automático, ~5 min)

Primera vez o cambio de infra
    └─► Deploy Infrastructure (manual, ~20 min)
            └─► Deploy App (automático al terminar)

Al terminar el lab
    └─► Cleanup (manual, escribir "ELIMINAR" para confirmar)
```

---

## Estructura del Repositorio

```
financial-compliance-rag-lab/
├── README.md                    # Este archivo
├── CONTRIBUTING.md              # Guía de contribución
├── DECISIONS.md                 # Architecture Decision Records (ADRs)
├── COST_ESTIMATE.md             # Estimación de costos
├── LICENSE                      # Apache 2.0
├── .gitignore
├── requirements.txt             # Dependencias principales
├── .env.example                 # Template de variables de entorno
│
├── .github/workflows/           # GitHub Actions CI/CD
│   ├── ci.yml                  # Validación en PRs y push a main
│   ├── deploy-infra.yml        # Deploy de infraestructura (manual)
│   ├── deploy-app.yml          # Deploy de app (automático en push)
│   └── cleanup.yml             # Eliminar recursos AWS (manual)
│
├── data/                        # Documentos de ejemplo (corpus)
│   ├── compliance/
│   │   ├── aml_policy.md        # Política Anti-Lavado de Dinero
│   │   └── kyc_procedures.md   # Procedimientos KYC
│   ├── onboarding/
│   │   ├── client_onboarding.md
│   │   └── employee_onboarding.md
│   └── policies/
│       ├── data_privacy.md
│       ├── risk_management.md
│       └── acceptable_use.md
│
├── ingestion/                   # Pipeline de ingesta
│   ├── config.py               # Configuración centralizada
│   ├── loader.py               # Carga de documentos
│   ├── chunker.py              # Estrategia de chunking
│   ├── embedder.py             # Embeddings con Titan V2
│   ├── store.py                # Escritura en pgvector
│   └── pipeline.py             # Orquestación completa
│
├── query/                       # Pipeline de consulta (RAG)
│   ├── retriever.py            # Búsqueda por similitud coseno
│   ├── memory.py               # Gestión de las 3 capas de memoria
│   ├── prompt_builder.py       # Construcción del prompt final
│   └── chain.py                # Orquestador RAG completo
│
├── app/
│   └── app.py                  # Interfaz Streamlit (3 columnas)
│
├── infrastructure/              # Infraestructura AWS
│   ├── README.md
│   ├── cloudformation.yaml     # Stack completo (VPC, Aurora, Redis, EC2...)
│   ├── setup.sh                # Provisiona infraestructura (~20 min)
│   ├── deploy-app.sh           # Despliega solo la app (~5 min)
│   └── cleanup.sh              # Elimina todos los recursos
│
├── scripts/                     # Scripts de utilidad
│   ├── init_db.py              # Inicializar schema pgvector
│   └── ingest_documents.py     # Ingesta de documentos al vector store
│
└── docs/
    └── github-actions-setup.md # Setup OIDC + IAM Role para CI/CD
```

---

## Cleanup — Evitar Costos

> **IMPORTANTE:** Siempre elimina los recursos después del lab. Aurora + ElastiCache cuestan ~$15-25 USD/día si se dejan corriendo.

**Script local:**
```bash
cd infrastructure && ./cleanup.sh
```

**GitHub Actions:** ve a `Actions → Cleanup → Run workflow` y escribe `ELIMINAR` en el campo de confirmación.

Ambas opciones eliminan: CloudFormation stack (VPC, Aurora, ElastiCache, EC2), bucket S3, tabla DynamoDB y secrets.

Ver estimación de costos detallada en [COST_ESTIMATE.md](COST_ESTIMATE.md).

---

## Preguntas de Ejemplo para el Demo

```
"¿Cuáles son los pasos del proceso KYC para clientes corporativos?"
"¿Qué documentos necesito para el onboarding de un cliente de alto riesgo?"
"¿Cuál es la política de retención de datos para registros de transacciones?"
"¿Cuándo es obligatorio hacer un Reporte de Actividad Sospechosa (SAR)?"
"¿Qué entrenamiento de compliance deben completar los empleados nuevos?"
```

---

## Contribuir

Ver [CONTRIBUTING.md](CONTRIBUTING.md).

## Seguridad

Ver [CONTRIBUTING.md#security-issue-notifications](CONTRIBUTING.md#security-issue-notifications).

## Licencia

Este proyecto está licenciado bajo Apache-2.0. Ver [LICENSE](LICENSE).
