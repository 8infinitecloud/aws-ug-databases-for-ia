# Architecture Decision Records (ADRs)

Cada decisión de arquitectura relevante está documentada aquí en formato ADR.  
**Para la presentación:** cada decisión tiene una oración resumen para comunicar en vivo.

---

## ADR-001: Vector Store — Aurora pgvector

**Frase para la presentación:** *"Usamos pgvector porque el equipo ya conoce PostgreSQL y no queremos un servicio más que operar."*

| Campo | Valor |
|-------|-------|
| **Título** | Elección del vector store |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

Necesitamos almacenar y buscar vectores de alta dimensionalidad (1024d) con búsqueda de similitud eficiente. El laboratorio debe ser reproducible en cuentas AWS estándar.

### Decisión Tomada

Aurora PostgreSQL Serverless v2 con la extensión `pgvector`.

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **Amazon OpenSearch Serverless** (k-NN) | Mayor costo base (~$700/mes mínimo en producción), más complejidad operacional |
| **Amazon S3 Vectors** | Servicio muy nuevo (re:Invent 2024), API aún en evolución, menos soporte en LangChain |
| **Amazon MemoryDB for Redis** | Bueno para baja latencia pero sin soporte nativo de pgvector SQL; indexación manual |
| **Pinecone / Weaviate (SaaS)** | Fuera de AWS, requiere gestión de creds externas, no es managed AWS |
| **FAISS en memoria** | No persiste entre reinicios, no escala horizontalmente |

### Consecuencias

- **Positivas:** SQL familiar para el equipo, joins posibles con datos relacionales, filtrado por metadata en la misma query, serverless escala a cero
- **Negativas:** Latencia ligeramente mayor que servicios especializados (OpenSearch k-NN), el índice HNSW de pgvector requiere tuning para millones de vectores
- **Riesgo mitigado:** Para el lab (<10K chunks) el rendimiento es más que suficiente; en producción con >1M docs escalar a OpenSearch o S3 Vectors

---

## ADR-002: Estrategia de Chunking

**Frase para la presentación:** *"Chunks de 1000 caracteres con 200 de overlap — suficiente para una respuesta coherente, pequeño suficiente para ser preciso."*

| Campo | Valor |
|-------|-------|
| **Título** | Estrategia de chunking de documentos |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

El chunking determina la granularidad con que el sistema recupera información. Chunks muy grandes → menos precisión en el retrieval. Chunks muy pequeños → contexto insuficiente para responder.

### Decisión Tomada

`RecursiveCharacterTextSplitter` de LangChain con:
- `chunk_size = 1000` caracteres
- `chunk_overlap = 200` caracteres
- Separadores: `["\n\n", "\n", ". ", " "]` (en ese orden de preferencia)

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **Chunking semántico** (por párrafos/secciones) | Requiere parsing específico por documento; los docs del lab son Markdown, OK para RecursiveCharacter |
| **chunk_size=512** | Demasiado pequeño para respuestas completas sobre procedimientos de compliance |
| **chunk_size=2000+** | Excede el contexto óptimo para Titan Embeddings; también hace el retrieval menos preciso |
| **Chunking por tokens** (tokenizer) | Añade dependencia de tokenizer; los docs son texto plano, caracteres son suficientes |

### Consecuencias

- El overlap del 20% asegura que procedimientos que se dividen entre chunks sigan siendo recuperables
- El separador `"\n\n"` preserva párrafos lógicos de los documentos de compliance
- Metadata por chunk: `source`, `page`, `chunk_index`, `doc_type` para filtrado post-retrieval

---

## ADR-003: Modelo de Embedding — Amazon Titan Embeddings V2

**Frase para la presentación:** *"Titan Embeddings V2 porque está en Bedrock, no necesitamos VPC endpoints adicionales y soporta español nativamente."*

| Campo | Valor |
|-------|-------|
| **Título** | Elección del modelo de embedding |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

El modelo de embedding determina la calidad de la búsqueda semántica. Debe soportar texto en español (políticas de la empresa), ser reproducible en AWS, y tener buena relación calidad/costo.

### Decisión Tomada

`amazon.titan-embed-text-v2:0` vía Amazon Bedrock.
- **Dimensiones:** 1024 (configurable a 256 o 512 para menor costo)
- **Input máximo:** 8,192 tokens
- **Normalización:** L2 por defecto

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **Cohere Embed Multilingual v3** | Buen rendimiento pero requiere llamada a Cohere API; disponible en Bedrock pero más caro |
| **text-embedding-3-small (OpenAI)** | Fuera de AWS; excelente calidad pero requiere API key externa |
| **Amazon Titan Embeddings V1** | Obsoleto; V2 tiene mejor rendimiento en multilingual |
| **all-MiniLM-L6-v2 (HuggingFace)** | Gratuito pero requiere inferencia en EC2; no managed |

### Consecuencias

- **Positivas:** Integrado en Bedrock (mismo IAM role), latencia baja desde misma región, soporta español
- **Negativas:** No el SOTA en benchmarks MTEB vs. modelos especializados
- **Costo:** $0.00002/1K tokens → muy económico para el lab y producción moderada

---

## ADR-004: Estrategia de Memoria — Tres Capas

**Frase para la presentación:** *"Tres capas porque tienen TTLs y costos distintos: Redis para lo inmediato, DynamoDB para lo persistente, pgvector para lo semántico."*

| Campo | Valor |
|-------|-------|
| **Título** | Arquitectura de memoria del asistente |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

Un asistente financiero debe mantener coherencia dentro de una conversación, recordar preferencias del usuario entre sesiones, y recuperar respuestas similares de sesiones pasadas.

### Decisión Tomada

Arquitectura de tres capas:

1. **Capa 1 — Memoria de Sesión (Redis):** Últimos N mensajes de la conversación activa. TTL: 2 horas. Estructura: lista con LPUSH/LTRIM.

2. **Capa 2 — Memoria de Usuario (DynamoDB):** Perfil del usuario, preferencias, resumen de sesiones pasadas. Sin TTL (persiste indefinidamente). Partition key: `user_id`.

3. **Capa 3 — Memoria Semántica (pgvector):** Embeddings de conversaciones pasadas. Permite recuperar "¿qué discutimos antes sobre KYC?" por similitud semántica.

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **Solo Redis para todo** | Sin persistencia a largo plazo; TTL elimina historial valioso |
| **Solo DynamoDB** | No soporta búsqueda semántica; recuperar por similitud requeriría escaneo completo |
| **Amazon Bedrock Memory** | Feature en beta/preview; menos control sobre TTL y estructura |
| **Una sola tabla DynamoDB** | Mezcla semántica y tabular; más difícil de razonar y escalar |

### Consecuencias

- El prompt final concatena las tres capas → mayor longitud de contexto, mayor costo por token
- Se mitiga con resumen automático de sesiones al cerrar (summary → DynamoDB, no raw messages)
- La capa 3 se consulta solo si las capas 1 y 2 no son suficientes (estrategia lazy)

---

## ADR-005: Orquestador RAG — LangChain

**Frase para la presentación:** *"LangChain porque su abstracción de chains hace el pipeline visible y educativo para la audiencia del lab."*

| Campo | Valor |
|-------|-------|
| **Título** | Framework de orquestación del pipeline RAG |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

El pipeline RAG tiene varios pasos (embed query → retrieve → build prompt → generate). Necesitamos un framework que sea familiar, bien documentado y que permita inspeccionar cada paso.

### Decisión Tomada

LangChain v0.3+ con LCEL (LangChain Expression Language).
- Integración nativa con Bedrock (`langchain-aws`)
- `ConversationalRetrievalChain` como base, extendida con memoria custom

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **LlamaIndex** | Igualmente válido; elegimos LangChain por mayor familiaridad en la comunidad hispana |
| **AWS Bedrock Knowledge Bases** | Abstrae demasiado para un lab educativo; no vemos los chunks ni scores |
| **Implementación custom (boto3 puro)** | Más control pero más código boilerplate; peor para el lab de 30 min |
| **Haystack** | Excelente para producción; curva de aprendizaje mayor para audiencia nueva |

### Consecuencias

- **Positivas:** Callbacks nativos permiten interceptar chunks/scores para mostrar en Streamlit
- **Negativas:** LangChain cambia API frecuentemente; fijar versiones en `requirements.txt`
- **LCEL** hace el pipeline composable y legible (útil para la presentación)

---

## ADR-006: Interfaz de Usuario — Streamlit

**Frase para la presentación:** *"Streamlit porque genera una UI interactiva con 100 líneas de Python, perfecta para demos en vivo."*

| Campo | Valor |
|-------|-------|
| **Título** | Framework de interfaz de usuario |
| **Fecha** | 2024-11-01 |
| **Estado** | Aceptado |

### Contexto

El lab necesita visualizar en tiempo real: chunks recuperados con scores, las tres capas de memoria, y la respuesta final con fuentes. La audiencia incluye desarrolladores que aprecian ver el código subyacente.

### Decisión Tomada

Streamlit 1.40+ con layout de tres columnas y `st.expander` para mostrar/ocultar detalles.

### Alternativas Consideradas

| Alternativa | Por qué se descartó |
|-------------|---------------------|
| **Gradio** | Similar a Streamlit; menos flexible para layout custom de 3 columnas |
| **React + FastAPI** | Demasiado overhead para un lab de 30 minutos |
| **Jupyter Notebook** | No permite UI interactiva real; outputs no se actualizan en tiempo real |
| **AWS Amplify + React** | Requiere deployment; demasiado para un lab local |

### Consecuencias

- Streamlit es single-threaded; no escala a múltiples usuarios simultáneos (OK para lab)
- `st.callback_handler` personalizado alimenta la UI con cada chunk recuperado
- En producción reemplazar con React + FastAPI o AWS Amplify
