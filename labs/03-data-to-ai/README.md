# Lab 3 — Data to AI: RAG sobre la Plataforma de Datos

> **Lab 3 de 4** · [← Lab 2](../02-transformation-quality-catalog/README.md) · [Índice](../../README.md) · [Lab 4 →](../04-streaming-online-insight/README.md)

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

> El lab se despliega sin los anteriores. El paso 5 (del pago a la respuesta)
> necesita los Labs 1 y 2 desplegados.

---

## La Historia Completa

En los labs anteriores construimos la plataforma de datos de FinCorp:

| Lab | Pregunta | Resultado |
|-----|---------|-----------|
| **Lab 1** | ¿Cómo llevamos cada cambio de producción a una plataforma? | S3 raw zone con las tablas del core, por DMS con CDC, y la cartera legacy |
| **Lab 2** | ¿Podemos confiar en esos datos? | S3 curated zone con Parquet validado, `source` por fila + Glue Catalog |
| **Lab 3** | ¿Cómo usamos esos datos para IA? | RAG assistant con 3 capas de memoria |
| **Lab 4** | ¿Podemos reaccionar ahora? | Scoring en milisegundos y telemetría de los terminales |

La idea central del workshop:

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

- Amazon Bedrock habilitado en `us-east-1`:
  - `amazon.titan-embed-text-v2:0`
  - `amazon.nova-pro-v1:0`
- AWS CLI v2, Terraform ≥ 1.6, `jq`
- Para el paso 5: Labs 1 y 2 desplegados y la tarea DMS en `running`

Comprueba el acceso a los modelos:

```bash
aws bedrock list-foundation-models --region us-east-1 \
  --query "modelSummaries[?contains(modelId,'titan-embed-text-v2')||contains(modelId,'nova-pro')].modelId"
```

Si no aparecen: **Bedrock Console → Model access → Request access**.

---

## Paso 1: Infraestructura (~20 min)

```bash
cd labs/03-data-to-ai/infrastructure

# Una contraseña tuya para Aurora (mínimo 8 caracteres)
export TF_VAR_db_password='...'

./setup.sh
```

Crea la VPC, Aurora PostgreSQL Serverless v2 con pgvector, ElastiCache Redis,
DynamoDB, el bucket de documentos, el secreto de Aurora y el EC2 de la app.
Aurora tarda ~8 minutos; el resto, menos.

Al terminar imprime la URL de Streamlit. Todavía no responde: falta el paso 2.

## Paso 2: Desplegar la app (~5 min)

```bash
./deploy-app.sh
```

1. Empaqueta el código y el corpus (`data/`) y los sube a S3.
2. Por SSM Run Command, el EC2 los descarga, crea el esquema de pgvector
   (`init_db.py`), ingesta los 11 documentos e inicia Streamlit.
3. Espera a que el EC2 termine su arranque si acabas de ejecutar `setup.sh`.

Aurora está en una subnet privada: la inicialización y la ingesta corren en el
EC2, que está en la VPC. Tu máquina no se conecta a Aurora.

Vuelve a ejecutarlo cada vez que cambies código o documentos.
`./deploy-app.sh --reingest` borra los chunks y reingesta el corpus completo.

## Paso 3: Comprobar

```bash
terraform -chdir=../../../infra/lab3 output streamlit_url
curl -sS -o /dev/null -w "HTTP %{http_code}\n" \
  "$(terraform -chdir=../../../infra/lab3 output -raw streamlit_url)"
```

Desde la raíz del repositorio, el semáforo completo:

```bash
python scripts/validate_setup.py --lab 3
```

Resultado esperado: `HTTP 200` y, en el log de `deploy-app.sh`,
`✓ 114 chunks almacenados en pgvector` (11 documentos).

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md#lab-3--data-to-ai)

---

## Paso 4: Preguntar

Abre la URL de Streamlit, elige una persona en la barra lateral y usa las
preguntas de ejemplo. Cada respuesta debe **citar sus fuentes**; el panel derecho
muestra los chunks recuperados, su score y las tres capas de memoria.

Todas usan el vocabulario exacto de los datos (`CARD_NOT_PRESENT`, `risk_level`,
`source`, `http_timeouts`...), que es lo que conecta la pregunta con el corpus
(ADR-017); un test comprueba que cada término exista en él.

**Sobre las políticas** (solo necesitan este lab):

```
¿Qué documentos necesito para afiliar a un comercio de riesgo alto?
¿Por qué un comercio en PENDING_KYC no puede cobrar?
¿Cuándo es obligatorio hacer un Reporte de Operaciones Sospechosas (ROS)?
¿Cuándo se considera caído un terminal?
```

**Las que atraviesan los cuatro labs** (el CDC del Lab 1 trajo los datos, el
Lab 2 los curó y separó por fuente, el Lab 4 trajo la telemetría):

```
¿Qué hago con un pago QR de un comercio con risk_level HIGH que supera el umbral?
¿Cómo reconozco un card testing en pagos CARD_NOT_PRESENT?
Un terminal tiene muchos http_timeouts: ¿falló la red o el emisor?
¿Qué hago con un registro rechazado por NULL_AMOUNT con source legacy?
```

La última responde que una operación en `rejected/` existió y no está siendo
vigilada (una brecha de compliance, no un problema de datos) y que viene de la
cartera absorbida. Es posible porque el Lab 2 conserva los rechazados con su
motivo y su `source`.

### Las capas de memoria

1. Haz 2-3 preguntas como **Ana García** (Ejecutiva de Afiliación).
2. Cambia a **Carlos Mendoza** (Analista de Monitoreo): nueva sesión en Redis,
   otro perfil en DynamoDB.
3. Vuelve a Ana: el asistente recupera el resumen de su sesión anterior.

Las preguntas de seguimiento (*"¿y a qué hora fueron?"*) funcionan dentro de una
sesión: la búsqueda se hace con la pregunta sola y con la anterior delante, y se
quedan los mejores resultados.

---

## Paso 5: Del pago a la respuesta

Requiere los Labs 1 y 2 desplegados. Desde la raíz del repositorio, en tu
máquina:

1. Haz uno o más pagos: con la tienda (`./scripts/store_local.sh`), el terminal
   Core2 o `python scripts/generate_traffic.py --api <api_endpoint> --count 20`.
2. Espera ~30 s: DMS escribe los cambios en `raw/acquiring/`.
3. Ejecuta:

   ```bash
   ./scripts/demo_cierre.sh
   ```

   | Paso | Qué hace | Dónde corre |
   |---|---|---|
   | 1 | Ejecuta el job de Glue del Lab 2: el CDC pasa a `curated/` | AWS Glue |
   | 2-3 | Genera los hechos derivados y los ingesta en pgvector | EC2 del Lab 3, por SSM |

   Tarda ~1 minuto. Tu máquina solo orquesta: no necesita `.env`, túnel ni
   `session-manager-plugin`.

4. En Streamlit, pregunta:

   ```
   ¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?
   ¿Qué comercios hicieron pagos hoy?
   ¿A qué hora se dieron y con qué montos?
   ```

   Resultado esperado: las cifras de los pagos del paso 1, citando
   `operaciones-actividad-hoy.md`. "Hoy" es el día en hora de Lima.

**Los hechos derivados** (`data/derived/`, generados por
`scripts/ingest_from_curated.py`):

| Documento | Contenido |
|---|---|
| `operaciones-actividad-hoy.md` | Pagos de hoy: total, aprobación, canal, moneda, comercios y el detalle de los 15 más recientes (hora, importe, resultado) |
| `operaciones-resumen-mensual.md` | Volumen e importe por mes |
| `operaciones-calidad-datos.md` | Rechazos por regla y por fuente (`acquiring` / `legacy`) |
| `operaciones-perfil-riesgo.md` | Distribución por `risk_level` y MCC |
| `operaciones-canales-emisor.md` | Aprobación por canal y códigos de rechazo del emisor |
| `operaciones-salud-terminales.md` | Terminales caídos, batería, timeouts (telemetría del Lab 4) |

Son agregados, no filas: el código calcula las cifras y el modelo solo las cita
(ADR-013). Reingestar un documento **sustituye** sus chunks en pgvector, así que
la cifra anterior no convive con la nueva.

Para que el RAG incorpore pagos nuevos, vuelve a ejecutar `demo_cierre.sh`: no
hay refresco automático.

---

## Cleanup

```bash
./cleanup.sh        # desde labs/03-data-to-ai/infrastructure
```

Aurora, Redis y el EC2 no escalan a cero: **~$2.86/día** si se quedan
encendidos. El script vacía el bucket de documentos, destruye el módulo
`infra/lab3` y borra el `.env` local si existe.

---

## Estructura de archivos

```
labs/03-data-to-ai/
├── ingestion/              # Pipeline de embeddings
│   ├── config.py           # Configuración centralizada (umbral de retrieval: 0.60)
│   ├── loader.py           # Carga de documentos Markdown y su doc_type
│   ├── chunker.py          # RecursiveCharacterTextSplitter
│   ├── embedder.py         # Titan Embeddings V2 con retry
│   ├── store.py            # pgvector (HNSW); reingestar sustituye por fichero
│   └── pipeline.py         # Orquestador completo
├── query/                  # Pipeline de consulta RAG
│   ├── retriever.py        # Búsqueda coseno en pgvector + fusión de búsquedas
│   ├── memory.py           # 3 capas: Redis + DynamoDB + pgvector
│   ├── prompt_builder.py   # Construcción del prompt con contexto
│   └── chain.py            # embed → search (+ pregunta anterior) → prompt → LLM
├── app/
│   └── app.py              # Streamlit: chat, personas y observabilidad RAG
├── data/
│   ├── compliance/         # aml_policy, kyc_procedures, monitoreo_operativo,
│   │                       # pagos_online, cartera_legacy
│   ├── operations/         # runbook_terminales
│   ├── onboarding/         # client_onboarding, employee_onboarding
│   ├── policies/           # data_privacy, risk_management, acceptable_use
│   └── derived/            # hechos generados (no versionados)
├── scripts/
│   ├── init_db.py          # Crea el esquema de pgvector
│   └── ingest_documents.py # Ingesta documentos (--source, --clear)
├── infrastructure/
│   ├── setup.sh            # Provisiona la infraestructura (~20 min)
│   ├── deploy-app.sh       # Despliega la app e ingesta el corpus (~5 min)
│   └── cleanup.sh          # Elimina todos los recursos
├── requirements.txt
└── .env.example            # Variables de la app (las escribe el EC2 al arrancar)
```

El código base viene del workshop "Databases for AI" (2025), accesible en el tag
`v1.0.0`.

---

## Desarrollo local

Los tests del retrieval y de los hechos derivados no necesitan AWS:

```bash
pytest tests/test_rag_retrieval.py tests/test_derived_docs.py -v
```

Para ejecutar la app en local hace falta un PostgreSQL con pgvector alcanzable y
acceso a Bedrock: ver [docs/local-development.md](../../docs/local-development.md).

---

| [← Lab 2](../02-transformation-quality-catalog/README.md) | [Índice](../../README.md) | [Lab 4 →](../04-streaming-online-insight/README.md) |
|---|---|---|
