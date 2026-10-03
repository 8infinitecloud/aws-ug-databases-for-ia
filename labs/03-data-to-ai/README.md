# Lab 3 — Data to AI: RAG sobre la Plataforma de Datos

> **Lab 3 de 3** · [← Lab 2](../02-transformation-quality-catalog/README.md) · [Índice](../../README.md)

| | |
|---|---|
| **Tiempo** | ~40 min (~20 son espera de Aurora) |
| **Coste** | ~$0.12/hora + ~$0.05 de Bedrock · **el lab más caro: haz cleanup** |
| **Qué despliegas** | Aurora pgvector, ElastiCache Redis, DynamoDB, S3, EC2 con Streamlit |
| **Requisitos** | Modelos de Bedrock habilitados. **No** necesita los Labs 1 y 2. |

**Objetivo:** montar un asistente de compliance con RAG y tres capas de memoria
sobre la plataforma de datos.

**Qué te llevas:** que el RAG es la última etapa y la más corta. Todo lo que lo
hace funcionar — datos accesibles, limpios y con significado — son los Labs 1 y 2.

> Este lab se puede hacer solo, sin los anteriores. Pero entonces te pierdes el
> punto del workshop.

---

## La Historia Completa

En los labs anteriores construimos la plataforma de datos de FinCorp:

| Lab | Pregunta | Resultado |
|-----|---------|-----------|
| **Lab 1** | ¿Cómo llevamos cada cambio de producción a una plataforma? | S3 raw zone con las tablas del core, por DMS con CDC, y la cartera legacy |
| **Lab 2** | ¿Podemos confiar en esos datos? | S3 curated zone con Parquet validado, `source` por fila + Glue Catalog |
| **Lab 3** | ¿Cómo usamos esos datos para IA? | RAG assistant con 3 capas de memoria |
| **Lab 4** | ¿Podemos reaccionar ahora? | Scoring en milisegundos y telemetría de los terminales |

El Lab 3 demuestra la idea central de la charla:

> *"RAG no comienza en el vector database. Comienza con la preparación de los datos."*

---

## El Problema

FinCorp tiene:
- Datos curados de transacciones (output del Lab 2, en S3 `curated/`)
- Documentos internos: políticas de compliance, KYC, procedimientos de onboarding
- Necesidad: un asistente que responda preguntas usando esos documentos como base de conocimiento

El equipo de compliance y de operaciones del adquirente hace preguntas como:
- *"¿Cuándo es obligatorio hacer un Reporte de Operaciones Sospechosas (ROS)?"*
- *"¿Cómo reconozco un card testing en pagos CARD_NOT_PRESENT?"*
- *"Un terminal tiene muchos http_timeouts: ¿falló la red o el emisor?"*

Sin un asistente, responder cada pregunta requiere buscar manualmente en documentos dispersos.

---

## Conexión con Labs 1 y 2

```
Lab 1: PostgreSQL del core ──DMS CDC──► S3 raw/acquiring/   (+ raw/legacy/)
                                              │
Lab 2: Glue ETL ──────────────────────────────┘
         │ aplica el CDC + une el legacy + valida + cataloga
         ▼
       S3 curated/transactions/  (source = acquiring | legacy)
                          │
                          │  scripts/ingest_from_curated.py:
                          │  los datos se convierten en HECHOS citables
                          ▼
Lab 3: DocumentLoader ──────────────► Aurora pgvector
         data/compliance/*.md              │ similarity search
         data/operations/*.md              ▼
         data/policies/*.md          RAG Chain (LangChain)
         data/derived/*.md  ← hechos       │ + 3 capas de memoria
                                           ▼
                                     Amazon Bedrock (Nova Pro)
                                           │
                                           ▼
                                     Streamlit UI
```

| Componente | Origen | Cómo llega al Lab 3 |
|-----------|--------|---------------------|
| Documentos de compliance, políticas y operación | `data/` directorio | `DocumentLoader` → chunks → pgvector |
| Schema del catálogo | Lab 2 `financial_platform` | Referencia en el corpus (puente léxico, ADR-017) |
| Datos curados y telemetría | Lab 2 `curated/`, Lab 4 `raw/telemetry/` | **Hechos derivados** con `ingest_from_curated.py`: resumen mensual, calidad por fuente, canales y emisor, salud de terminales, actividad de hoy (ADR-013) |

---

## Arquitectura

```
┌──────────────────────────────────────────────────────────────────┐
│                    USUARIO (Streamlit UI)                         │
└─────────────────────────┬────────────────────────────────────────┘
                          │ pregunta
           ┌──────────────▼──────────────┐
           │      LangChain RAG Chain     │
           │      (query/chain.py)        │
           └──┬──────────┬───────────┬───┘
              │          │           │
   ┌──────────▼──┐  ┌────▼─────┐  ┌─▼──────────────┐
   │  CAPA 1:    │  │  CAPA 2: │  │  CAPA 3:        │
   │  Sesión     │  │  Usuario │  │  Semántica      │
   │             │  │          │  │                  │
   │ ElastiCache │  │ DynamoDB │  │ Aurora pgvector  │
   │ Redis       │  │          │  │ (memorias de     │
   │ (últimos N  │  │(perfil + │  │  conversaciones) │
   │  mensajes)  │  │ resumen) │  │                  │
   └─────────────┘  └──────────┘  └──────────────────┘
                                          │
              ┌───────────────────────────┘
              │  Vector Store (RAG)
              │  Aurora PostgreSQL + pgvector
              │  tabla: document_chunks
              │  Búsqueda coseno → Top-K chunks
              │
   ┌──────────▼──────────────────┐
   │       Amazon Bedrock         │
   │  Titan Embeddings V2 (1024d) │
   │  Amazon Nova Pro / Claude    │
   └─────────────────────────────┘
```

---

## Tres Capas de Memoria

| Capa | Servicio | TTL | Para qué |
|------|----------|-----|----------|
| 1 — Sesión | ElastiCache Redis | 2 horas | Historial de mensajes de la sesión activa |
| 2 — Persistente | DynamoDB | Sin TTL | Perfil del usuario, resúmenes de sesiones anteriores |
| 3 — Semántica | Aurora pgvector | Sin TTL | Embeddings de conversaciones pasadas; búsqueda por similitud |

La Capa 3 es el diferenciador: permite que el asistente recuerde *el contenido* de conversaciones anteriores (no solo el historial), buscando semánticamente qué conversaciones pasadas son relevantes para la pregunta actual.

---

## Servicios AWS

| Servicio | Rol |
|----------|-----|
| Amazon Bedrock (Titan Embeddings V2) | Genera embeddings de documentos y queries |
| Amazon Bedrock (Nova Pro / Claude) | Genera las respuestas en lenguaje natural |
| Aurora PostgreSQL Serverless v2 + pgvector | Vector store (RAG) + memoria semántica |
| Amazon ElastiCache Redis | Capa 1: memoria de sesión |
| Amazon DynamoDB | Capa 2: memoria persistente de usuario |
| Amazon S3 | Almacenamiento de documentos fuente |
| AWS Secrets Manager | Credenciales Aurora (nunca en texto plano) |
| Amazon EC2 | App server con Streamlit |
| AWS SSM | Deploy sin SSH (deploy-app.sh) |

---

## Prerrequisitos

- Labs 1 y 2 completados (no estrictamente requerido, pero narración asume que sí)
- Amazon Bedrock habilitado en `us-east-1`:
  - `amazon.titan-embed-text-v2:0`
  - `amazon.nova-pro-v1:0` (o `anthropic.claude-3-5-sonnet-20241022-v2:0`)
- AWS CLI v2
- Python 3.11+

### Habilitar modelos en Bedrock

```bash
aws bedrock list-foundation-models \
  --by-provider amazon \
  --query "modelSummaries[?contains(modelId, 'titan-embed-text-v2')]" \
  --region us-east-1
```

Si no tienes acceso: **Bedrock Console → Model access → Request access**.

---

## Deployment

### Paso 1: Infraestructura (~20 min — preparar ANTES del demo)

```bash
cd labs/03-data-to-ai/infrastructure

# Elige una tuya: esta la vería cualquiera que lea este repositorio.
export TF_VAR_db_password="CambiaEstaPassword2026!"
export AWS_REGION="us-east-1"

chmod +x setup.sh
./setup.sh
```

Crea: VPC, Aurora PostgreSQL Serverless v2 + pgvector, ElastiCache Redis, DynamoDB, S3, EC2 App Server, Secrets Manager.

> Este paso tarda ~20 minutos. Ejecútalo antes del demo en vivo.

### Paso 2: Inicializar DB e ingestar documentos (~10 min)

```bash
cd labs/03-data-to-ai

cp .env.example .env
# Editar .env con AURORA_HOST, REDIS_HOST, etc.

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python scripts/init_db.py
python scripts/ingest_documents.py --source data/
```

> El `.env` va dentro de `labs/03-data-to-ai/`, no en la raíz: es donde
> `cleanup.sh` lo busca para borrarlo al final.

### Paso 3: Desplegar la app (~5 min)

```bash
./infrastructure/deploy-app.sh
```

Empaqueta el código, lo sube a S3, lo ejecuta en EC2 via SSM. Al terminar imprime la URL de Streamlit.

### Desarrollo local (sin EC2)

```bash
cd labs/03-data-to-ai

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # completar con outputs del stack Lab 3

python scripts/init_db.py
python scripts/ingest_documents.py --source data/
streamlit run app/app.py  # http://localhost:8501
```

---

## ✅ Checkpoint — ¿funcionó?

```bash
# 1. La infraestructura está en pie
terraform -chdir=../../infra/lab3 output

# 2. Streamlit responde
curl -sSf -o /dev/null -w "HTTP %{http_code}\n" \
  "$(terraform -chdir=../../infra/lab3 output -raw streamlit_url)"
```

Y desde la raíz del repositorio, el semáforo completo:

```bash
python scripts/validate_setup.py --lab 3
```

Abre la URL de Streamlit y haz una pregunta. Debe responder **citando las
fuentes** — si responde sin fuentes o dice que no encuentra nada, el corpus no
se ingestó.

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md#lab-3--data-to-ai)

---

## Preguntas de demo

Las mismas que la app ofrece a cada persona. Todas usan el vocabulario exacto de
los datos (`CARD_NOT_PRESENT`, `risk_level`, `source`, `http_timeouts`...), que es
lo que conecta la pregunta con el corpus (ADR-017); un test comprueba que cada
término exista en él.

**Sobre las políticas**, que solo usan el Lab 3:

```
"¿Qué documentos necesito para afiliar a un comercio de riesgo alto?"
"¿Por qué un comercio en PENDING_KYC no puede cobrar?"
"¿Cuándo es obligatorio hacer un Reporte de Operaciones Sospechosas (ROS)?"
"¿Cuándo se considera caído un terminal?"
```

**Las que atraviesan los cuatro labs.** La respuesta solo es posible porque la
plataforma existe: el CDC del Lab 1 trajo los datos, el Lab 2 los curó y separó
por fuente, y el Lab 4 trajo la telemetría:

```
"¿Qué hago con un pago QR de un comercio con risk_level HIGH que supera el umbral?"
"¿Cómo reconozco un card testing en pagos CARD_NOT_PRESENT?"
"Un terminal tiene muchos http_timeouts: ¿falló la red o el emisor?"
"¿Qué hago con un registro rechazado por NULL_AMOUNT con source legacy?"
```

> La última sigue siendo la que mejor explica el taller: la respuesta dice que
> una operación en `rejected/` **existió y no está siendo vigilada**, así que es
> una brecha de compliance, no un problema de datos, y que viene de la cartera
> absorbida, no de nuestro core. Eso solo se puede responder porque el Lab 2
> conservó los rechazados con su motivo y con su `source`.

### El cierre en vivo

```bash
./scripts/demo_cierre.sh
```

Glue cura lo que el CDC trajo, los datos se convierten en hechos y entran al
vector store. Los hechos se generan e ingestan **en el EC2 del Lab 3** (por SSM
Run Command), que está dentro de la VPC y alcanza Aurora: tu portátil solo
orquesta, sin túnel ni `.env` local. Tarda ~1 minuto. Después, la pregunta:

```
"¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?"
```

**El pago que alguien de la sala hizo hace cinco minutos está en la respuesta.**
El script mide cuánto tarda cada paso: es lo que se narra mientras corre. Si
falla en vivo, se usa la respuesta grabada en el ensayo; no se reintenta delante
del público.

Reingestar un documento **sustituye** sus chunks en pgvector. Antes los
acumulaba (los ids eran aleatorios), y la cifra de un ensayo anterior habría
convivido con la nueva.

Para demostrar las capas de memoria:
1. Haz 2-3 preguntas como **Ana García** (Ejecutiva de Afiliación)
2. Cambia a **Carlos Mendoza** (Analista de Monitoreo) — nueva sesión Redis, perfil DynamoDB diferente
3. Regresa a Ana — observa que el sistema "recuerda" el resumen de la sesión anterior

---

## Cleanup

```bash
cd infrastructure && ./cleanup.sh
```

> **IMPORTANTE**: Aurora + ElastiCache cuestan ~$15-25 USD/día si se dejan corriendo.  
> Siempre hacer cleanup al terminar el lab o el demo.

---

## Estructura de archivos

El código de este lab viene de la charla anterior, "Databases for AI" (AWS
Community Day Perú 2025), y se conserva sin cambios funcionales. El repositorio
de 2025 íntegro sigue accesible en el tag `v1.0.0`.

```
labs/03-data-to-ai/
├── ingestion/              # Pipeline de embeddings
│   ├── config.py          # Configuración centralizada
│   ├── loader.py          # Carga de documentos Markdown
│   ├── chunker.py         # RecursiveCharacterTextSplitter
│   ├── embedder.py        # Titan Embeddings V2 con retry
│   ├── store.py           # pgvector (HNSW index)
│   └── pipeline.py        # Orquestador completo
├── query/                  # Pipeline de consulta RAG
│   ├── retriever.py       # Búsqueda coseno en pgvector
│   ├── memory.py          # 3 capas: Redis + DynamoDB + pgvector
│   ├── prompt_builder.py  # Construcción del prompt con contexto
│   └── chain.py           # Orquestador RAG (embed → search → prompt → LLM)
├── app/
│   └── app.py             # Streamlit UI con chat + observabilidad RAG
├── data/
│   ├── compliance/        # aml_policy.md, kyc_procedures.md
│   ├── onboarding/        # client_onboarding.md, employee_onboarding.md
│   └── policies/          # data_privacy.md, risk_management.md, acceptable_use.md
├── scripts/
│   ├── init_db.py         # Crea schema pgvector
│   └── ingest_documents.py # Ingesta documentos con --source y --clear
├── infrastructure/
│   ├── setup.sh            # Provisiona infraestructura (~20 min)
│   ├── deploy-app.sh       # Despliega solo la app (~5 min)
│   └── cleanup.sh          # Elimina todos los recursos
├── requirements.txt        # Dependencias del Lab 3
└── .env.example            # Template de variables de entorno
```

---

← **Lab 2:** `labs/02-transformation-quality-catalog/README.md`

---

## La plataforma completa — diagrama de cierre

Este es el diagrama que se revela al final de la charla, después de los 3 labs. Muestra todos los servicios en contexto:

```
┌──────────────────────────────────────────────────────────────────────┐
│  SERVICIOS REVELADOS DURANTE LOS 3 LABS                               │
├───────────────────────────────────────────────────────────────────────┤
│                                                                        │
│  PostgreSQL core ─[DMS CDC]─► S3 raw/  ← LAB 1: AWS DMS + Amazon S3  │
│                           │                                            │
│                      [Glue ETL]  ← LAB 2: AWS Glue Python Shell       │
│                           │       AWS Glue Data Quality (DQDL)         │
│                     S3 curated/  AWS Glue Data Catalog                 │
│                     ┌─────┤                                            │
│                     │     │                                            │
│                  Athena   │       ← (Analytics serverless)             │
│                           │                                            │
│              [Bedrock Titan] → Aurora pgvector  ← LAB 3               │
│              [Bedrock Nova]  → Redis (sesión)                          │
│                           │  → DynamoDB (usuario)                     │
│                           │  → Streamlit (UI)                         │
│                           │                                            │
└──────────────────────────────────────────────────────────────────────┘
```

## Evolución desde la charla anterior (2025)

```
ANTES — Databases for AI (2025):
  Base de datos → pgvector → RAG → AI

AHORA — Data Platform for AI (2026):
  Datos distribuidos
    → DMS (ingestion)
    → S3 raw (aterrizaje)
    → Glue (transformación + calidad)
    → S3 curated (datos confiables)
    → Glue Catalog (contexto y metadata)
    → pgvector / RAG (consumo por IA)   ← mismo punto de llegada que antes
    → AI

El Lab 3 es el mismo que en 2025. Lo que cambió es todo lo que lo precede.
```


---

## Has terminado el workshop

Recorriste el camino completo: de una base de datos de producción a un asistente
de IA que responde sobre datos en los que puedes confiar. Sin saltarte la parte
aburrida, que es justo la que hace que la IA funcione.

### ⚠️ Cleanup — hazlo ahora

Este lab es el más caro: Aurora, Redis y EC2 no escalan a cero. **~$2.86/día si
lo dejas.**

```bash
bash labs/03-data-to-ai/infrastructure/cleanup.sh
bash labs/02-transformation-quality-catalog/scripts/cleanup.sh
bash labs/01-ingestion-data-lake/scripts/cleanup.sh
```

Comprueba que no queda nada:

```bash
for M in payments lab1 lab2 lab3 lab4; do
  echo -n "infra/$M: "
  terraform -chdir="infra/$M" state list 2>/dev/null | wc -l
done
# Los tres deben dar 0
```

### Para seguir

- **Lake Formation** — control de acceso a nivel de fila y columna sobre el catálogo
- **Catalog Federation** — consultar Redshift o Databricks desde Athena sin mover datos
- **Ingestar el `curated/` del Lab 2 al vector store** — hoy la conexión Lab 2 → Lab 3
  es narrativa; hacerla técnica es un buen ejercicio
- **Agentes** — el asistente responde preguntas; un agente además actúa

| [← Lab 2](../02-transformation-quality-catalog/README.md) | [Índice](../../README.md) | [Costes](../../COST_ESTIMATE.md) |
|---|---|---|
