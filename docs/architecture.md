# ARCHITECTURE — Data Platform for AI en AWS

> Este documento describe la arquitectura **implementada**. Para componentes en
> evaluación y no construidos, ver
> [architecture-demo-proposal.md](architecture-demo-proposal.md).

> Arquitectura completa de la charla "De los Datos a la IA"  
> Ver `docs/STRATEGY.md` para el contexto narrativo

---

## Arquitectura completa de la plataforma

```mermaid
flowchart TD
    subgraph SOURCES["📦 DATA SOURCES"]
        RDS[(RDS MySQL\nDatos transaccionales)]
        FILES[Archivos / S3\nDocumentos no estructurados]
        STREAM[Streams\nEventos en tiempo real]
    end

    subgraph INGESTION["⬇️ INGESTION — Lab 1"]
        DMS[AWS DMS\nDatabase Migration Service]
        S3RAW[(Amazon S3\nRaw Zone)]
    end

    subgraph TRANSFORM["⚙️ TRANSFORMATION + QUALITY + CATALOG — Lab 2"]
        GLUE[AWS Glue\nETL Jobs PySpark]
        GDQ[Glue Data Quality\nValidación y reglas]
        CATALOG[Glue Data Catalog\nMetadata + Schema]
        S3CURATED[(Amazon S3\nCurated Zone)]
    end

    subgraph GOVERNANCE["🔒 GOVERNANCE"]
        LF[AWS Lake Formation\nControl de acceso]
    end

    subgraph CONSUME["🎯 CONSUMPTION — Lab 3"]
        direction LR
        subgraph ANALYTICS["Analytics"]
            ATHENA[Amazon Athena\nQuery SQL]
            REDSHIFT[Amazon Redshift\nData Warehouse]
        end
        subgraph AI["AI / GenAI"]
            BEDROCK[Amazon Bedrock\nClaude + Titan Embeddings]
            AURORA[(Aurora PostgreSQL\npgvector)]
            DYNAMO[(DynamoDB\nMemoria persistente)]
            REDIS[(ElastiCache Redis\nMemoria de sesión)]
            APP[Streamlit App\nRAG Assistant]
        end
    end

    RDS -->|Replicación CDC| DMS
    DMS -->|Aterrizaje| S3RAW
    FILES -->|Upload| S3RAW

    S3RAW -->|Lee raw data| GLUE
    GLUE -->|Valida| GDQ
    GLUE -->|Registra schema| CATALOG
    GLUE -->|Escribe curated| S3CURATED

    S3CURATED --> LF
    LF --> ATHENA
    LF --> REDSHIFT

    S3CURATED -->|Documentos curados| BEDROCK
    FILES -->|Documentos directos| BEDROCK
    BEDROCK -->|Embeddings| AURORA
    AURORA -->|Similarity search| APP
    DYNAMO -->|User memory| APP
    REDIS -->|Session memory| APP
    BEDROCK -->|LLM response| APP
```

---

## Arquitectura por lab

### Lab 1 — Ingestion + Data Lake

```mermaid
flowchart LR
    subgraph SOURCE["Fuente"]
        RDS[(RDS MySQL\nFinancial DB)]
    end

    subgraph DMS_BLOCK["AWS DMS"]
        REP[Replication Instance]
        TASK[Replication Task\nFull Load + CDC]
    end

    subgraph S3_BLOCK["Amazon S3 — Data Lake"]
        RAW["s3://bucket/raw/\ntransactions/\ncustomers/"]
    end

    RDS -->|Conexión source endpoint| REP
    REP --> TASK
    TASK -->|Escribe Parquet/CSV| RAW

    style SOURCE fill:#fff3cd
    style DMS_BLOCK fill:#cfe2ff
    style S3_BLOCK fill:#d1e7dd
```

**Componentes del Lab 1:**
| Recurso | Tipo | Propósito | Costo estimado demo |
|---------|------|-----------|---------------------|
| RDS MySQL db.t3.micro | EC2 | Fuente de datos transaccional | ~$0.02/hr |
| DMS Replication Instance dms.t3.medium | Managed | Motor de replicación | ~$0.07/hr |
| S3 bucket | Object Storage | Raw zone del Data Lake | <$0.01 |
| Secrets Manager | Managed | Credenciales RDS | $0.01/hr |

---

### Lab 2 — Transformation + Quality + Catalog

```mermaid
flowchart TD
    S3RAW["S3 Raw\ntransactions con problemas\nnulos / duplicados / tipos inválidos"]

    subgraph GLUE_JOB["AWS Glue Job (PySpark)"]
        READ[Leer S3 raw]
        CLEAN[Transformar\ntipos, fechas, nulos]
        VALIDATE[Aplicar reglas\nGlue Data Quality]
        WRITE[Escribir curated\nformato Parquet + particiones]
    end

    subgraph CATALOG_BLOCK["Glue Data Catalog"]
        CRAWLER[Crawler\nauto-descubre schema]
        DB[Database\nfinancial_platform]
        TABLE[Table\ntransactions_curated]
        META[Metadata\nowner, quality score, source]
    end

    S3CURATED["S3 Curated\ntransactions/\nyear=2026/month=09/"]

    QUALITY_REPORT["Reporte de Calidad\n✓ completeness\n✓ uniqueness\n✗ validity (algunos rechazados)"]

    S3RAW --> READ
    READ --> CLEAN
    CLEAN --> VALIDATE
    VALIDATE --> WRITE
    WRITE --> S3CURATED
    VALIDATE --> QUALITY_REPORT
    S3CURATED --> CRAWLER
    CRAWLER --> DB
    DB --> TABLE
    TABLE --> META

    style S3RAW fill:#fff3cd
    style S3CURATED fill:#d1e7dd
    style QUALITY_REPORT fill:#f8d7da
```

**Problemas deliberados en el dataset (para demostrar Data Quality):**
- `amount = NULL` en ~5% de transacciones
- `customer_id` duplicado en ~3% de registros
- `transaction_date` en formato incorrecto (`DD/MM/YYYY` vs `YYYY-MM-DD`)
- `transaction_type` con valores no válidos (`'DEPÓSITO'` vs `'DEPOSIT'`)
- `status` con inconsistencias de case (`'pending'` / `'PENDING'` / `'Pending'`)

---

### Lab 3 — Data to AI

```mermaid
flowchart TD
    subgraph INPUT["Entradas"]
        CURATED["S3 Curated\n(output Lab 2)"]
        DOCS["Documentos no estructurados\ncompliance / policies / onboarding"]
    end

    subgraph INGESTION_BLOCK["Pipeline de Ingesta (Embeddings)"]
        LOADER[Document Loader]
        CHUNKER[Text Splitter\nchunk_size=1000, overlap=200]
        EMBEDDER[Amazon Titan Embeddings V2\n1024 dimensiones]
        STORE[Aurora pgvector\ntabla: document_chunks]
    end

    subgraph MEMORY["Tres Capas de Memoria"]
        REDIS_M["Capa 1: ElastiCache Redis\nMemoria de sesión\nÚltimos N mensajes"]
        DYNAMO_M["Capa 2: DynamoDB\nMemoria persistente\nPerfil + resumen de sesiones"]
        PGVECTOR_M["Capa 3: Aurora pgvector\nMemoria semántica\nEmbeddings de conversaciones"]
    end

    subgraph RAG_CHAIN["RAG Chain (LangChain)"]
        EMBED_Q[Embed query\nTitan V2]
        SEARCH[Similarity Search\nTop-K chunks + scores]
        PROMPT[Prompt Builder\ncontexto + memoria + query]
        LLM[Amazon Bedrock\nClaude Sonnet]
    end

    APP_UI["Streamlit UI\nFinancial Compliance Assistant"]

    DOCS --> LOADER
    LOADER --> CHUNKER
    CHUNKER --> EMBEDDER
    EMBEDDER --> STORE

    APP_UI -->|pregunta| EMBED_Q
    EMBED_Q --> SEARCH
    SEARCH -->|chunks relevantes| PROMPT
    REDIS_M -->|historial sesión| PROMPT
    DYNAMO_M -->|perfil usuario| PROMPT
    PGVECTOR_M -->|memorias pasadas| PROMPT
    PROMPT --> LLM
    LLM -->|respuesta| APP_UI

    style INPUT fill:#fff3cd
    style MEMORY fill:#e8d5f5
    style RAG_CHAIN fill:#cfe2ff
```

---

## Arquitectura de reveal final (slide de cierre)

```mermaid
flowchart TD
    DS[DATA SOURCES\nRDS · Files · Streams]
    ING[INGESTION\nAWS DMS]
    RAW[RAW DATA\nAmazon S3]
    TRF[TRANSFORMATION\nAWS Glue]
    DQ[DATA QUALITY\nGlue Data Quality]
    CAT[CATALOG / METADATA\nGlue Data Catalog]
    GOV[GOVERNANCE\nLake Formation]
    CUR[CURATED DATA\nAmazon S3]

    ANA[ANALYTICS\nAthena · Redshift]
    AI_BLOCK[AI / GenAI\nBedrock · pgvector\nDynamoDB · Redis]
    RAG_BLOCK[RAG\nLangChain + Claude]
    AGE[AGENTS\nfuture]

    DS --> ING --> RAW --> TRF --> DQ --> CAT --> GOV --> CUR
    CUR --> ANA
    CUR --> AI_BLOCK --> RAG_BLOCK --> AGE

    style DS fill:#fff3cd
    style ING fill:#cfe2ff
    style RAW fill:#cfe2ff
    style TRF fill:#d1e7dd
    style DQ fill:#d1e7dd
    style CAT fill:#d1e7dd
    style GOV fill:#f8d7da
    style CUR fill:#d1e7dd
    style ANA fill:#e8d5f5
    style AI_BLOCK fill:#e8d5f5
    style RAG_BLOCK fill:#e8d5f5
    style AGE fill:#f0f0f0
```

---

## Mapa de servicios AWS

| Capa | Servicio | Rol en la plataforma |
|------|----------|---------------------|
| Ingestion | AWS DMS | Replica datos de RDS → S3 con CDC |
| Storage | Amazon S3 | Raw zone + Curated zone del Data Lake |
| Transformation | AWS Glue (PySpark) | ETL: raw → limpio → curated |
| Data Quality | Glue Data Quality | Validación con ruleset declarativo |
| Catalog | Glue Data Catalog | Schema, metadata, business context |
| Governance | AWS Lake Formation | Control de acceso a nivel tabla/columna |
| Analytics | Amazon Athena | SQL serverless sobre S3 curated |
| Vector Store | Aurora PostgreSQL + pgvector | Embeddings de documentos y memorias |
| Session Memory | Amazon ElastiCache Redis | Últimos N mensajes de la sesión |
| Persistent Memory | Amazon DynamoDB | Perfil y resumen de usuario |
| LLM + Embeddings | Amazon Bedrock | Claude (generación) + Titan (embeddings) |

> **Principio de inclusión**: cada servicio aparece porque resuelve un problema demostrado en el lab, no porque sea un servicio interesante de explicar.

---

## Relación con la arquitectura de la charla 2025 (v1.0.0)

El código heredado de 2025 implementa el Lab 3 completo, y hoy vive en
`labs/03-data-to-ai/`:

| Módulo (2025) | Rol en la nueva arquitectura |
|---------------|------------------------------|
| `labs/03-data-to-ai/ingestion/` | Pipeline de embeddings → Lab 3 |
| `labs/03-data-to-ai/query/` | RAG chain + 3 capas de memoria → Lab 3 |
| `labs/03-data-to-ai/app/app.py` | Streamlit UI → Lab 3 |
| `labs/03-data-to-ai/data/` | Corpus de documentos → Lab 3 |
| `infra/lab3/` | Base para extender con Lab 1 y 2 |

El código **no se elimina ni se duplica**: es el Lab 3, y vive en
`labs/03-data-to-ai/`. El repositorio de 2025 íntegro sigue accesible en el tag
`v1.0.0`.

---

*Ver `docs/DESIGN.md` para las decisiones de diseño específicas por lab.*
