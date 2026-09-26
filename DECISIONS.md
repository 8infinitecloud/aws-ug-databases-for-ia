# Architecture Decision Records (ADRs)

Cada decisión de arquitectura relevante está documentada aquí en formato ADR.  
**Para la presentación:** cada decisión tiene una oración resumen para comunicar en vivo.

| ADRs | Lab | Tema |
|------|-----|------|
| 001–006 | Lab 3 | RAG: vector store, chunking, embeddings, memoria, orquestación, UI |
| 007–009 | Lab 1 | Ingesta: DMS, zonas del data lake, dataset sintético |
| 010–013 | Lab 2 | Glue Python Shell, medición en tres etapas, rechazo vs corrección, y cómo llega a la IA |
| 014–016 | Transversal | Terraform, state remoto, acoplamiento entre labs |

Las decisiones que se apartan a propósito de lo que pediría producción están en
[docs/well-architected.md](docs/well-architected.md).

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

---

## ADR-007: Ingesta — AWS DMS en vez de un ETL propio

**Frase para la presentación:** *"No escribimos un solo script de extracción: DMS lee el binlog de MySQL y escribe en S3, sin tocar la base de datos de producción."*

| Campo | Valor |
|-------|-------|
| **Título** | Mecanismo de ingesta desde el sistema origen |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

FinCorp tiene sus transacciones en un MySQL de producción. Analytics necesita esos datos sin degradar la base operativa, y sin que el equipo de datos tenga que pedir permiso cada vez que cambia el esquema.

### Decisión Tomada

AWS DMS con `MigrationType = full-load-and-cdc`: primero copia las tablas, luego se queda leyendo el binlog.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Script con `mysqldump` en cron** | Snapshot completo cada vez; carga sobre producción; no capta cambios entre ejecuciones |
| **AWS Glue con conexión JDBC** | Consulta la base directamente en cada run, y eso es carga sobre producción. Sin CDC. |
| **Zero-ETL de Aurora a Redshift** | Requiere Aurora, no MySQL en RDS. Y salta la zona `raw/`, que es lo que el lab quiere enseñar. |
| **Debezium + Kafka (MSK)** | Es la respuesta correcta a gran escala, pero añade dos servicios que operar y 40 minutos al lab |

### Consecuencias

- CDC obliga a `backup_retention_period >= 1` en RDS: sin backups automáticos, MySQL no retiene el binlog. Es el fallo más común al montar este lab.
- Los parámetros `binlog_format = ROW` y `binlog_row_image = Full` van en un parameter group, no en la instancia.
- La instancia de replicación es el recurso más lento (~10 min) y el más caro del Lab 1 (~$0.068/h). También el que más se olvida en el cleanup, porque no aparece en la consola de RDS.

---

## ADR-008: Un solo bucket con prefijos, no un bucket por zona

**Frase para la presentación:** *"Las zonas del data lake son `raw/` y `curated/` dentro del mismo bucket: la separación que importa es conceptual, no física."*

| Campo | Valor |
|-------|-------|
| **Título** | Estructura del data lake |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

Un data lake necesita zonas: datos como llegaron, datos transformados, datos rechazados. La pregunta es si se separan por bucket, por cuenta, o por prefijo.

### Decisión Tomada

Un bucket con cuatro prefijos: `raw/`, `curated/`, `rejected/`, `quality-reports/`.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Un bucket por zona** | Tres buckets, tres políticas, tres ciclos de vida. Nada de eso enseña algo nuevo. |
| **Una cuenta AWS por zona** | Es lo correcto con varios equipos y datos sensibles, pero imposible de montar en un lab |
| **Lake Formation desde el principio** | Añade ~1 hora al setup y la lección de governance se pierde entre la configuración |

### Consecuencias

- Los cuatro prefijos se ven de una vez en la consola: la progresión `raw → curated → rejected` es visible sin cambiar de pantalla.
- El Lab 2 escribe en el bucket que crea el Lab 1, así que **el cleanup tiene un orden obligatorio**: Lab 2 antes que Lab 1.
- Ciclos de vida por prefijo: `raw/` a IA a los 90 días, `curated/` a los 180.

---

## ADR-009: Dataset sintético calibrado, no datos reales

**Frase para la presentación:** *"El dataset es sintético, pero los problemas son reales: importes nulos, IDs duplicados, tipos en dos idiomas. Lo sintético es el control, no el problema."*

| Campo | Valor |
|-------|-------|
| **Título** | Origen del dataset del workshop |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

El Lab 2 mide 8 reglas de calidad. Para que cada una enseñe algo, tiene que comportarse de una forma predecible: una que pase al borde del umbral, una que falle sin posibilidad de arreglo, dos que se arreglen normalizando.

### Decisión Tomada

Dataset sintético con cantidades exactas de cada problema, generado por `labs/01-ingestion-data-lake/dataset/generate_dataset.py` con semilla fija.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **PaySim (6.3M transacciones reales simuladas)** | Viene limpio: el Lab 2 no rechazaría nada. Buena opción si se quiere volumen; ver [docs/datasets.md](docs/datasets.md). |
| **Datos abiertos del Estado peruano** | Vienen sucios de verdad, que es una ventaja, pero no se sabe de antemano qué reglas fallan. Inviable con tiempo medido. |
| **NYC TLC** | Ya en Parquet en S3, pero no es financiero y se pierde la narrativa de compliance |
| **Datos reales anonimizados** | No los hay, y anonimizar bien es un proyecto aparte |

### Consecuencias

- Los números están en la documentación y en los tests: 50 nulos, 55 filas duplicadas, 100 rechazadas. `tests/test_glue_job.py` los fija.
- Regenerar el dataset **no** reproduce los CSV byte a byte, solo sus porcentajes. Los CSV commiteados son la referencia.
- `Completeness "amount" >= 0.95` con exactamente 50 nulos sobre 1000 da 0.9500: pasa por un pelo. Es deliberado, para mostrar que un umbral es una decisión y no una verdad.

---

## ADR-010: Glue Python Shell en vez de PySpark

**Frase para la presentación:** *"Mil filas caben en pandas. Elegir Spark aquí sería el error de diseño, no lo contrario."*

| Campo | Valor |
|-------|-------|
| **Título** | Motor de transformación del Lab 2 |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

Hay que transformar ~1000 filas y validarlas. Glue ofrece Python Shell (pandas) y Spark (`glueetl`).

### Decisión Tomada

Python Shell con `max_capacity = 0.0625` DPU.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Glue PySpark (`glueetl`)** | Arranca en ~3 minutos contra ~20 segundos. En una demo en vivo, esos 3 minutos son la diferencia entre enseñar algo y esperar. Y para 1000 filas Spark no aporta nada. |
| **Lambda** | 15 minutos de límite y sin integración nativa con el Data Catalog |
| **Athena CTAS** | Elegante para transformar, pero no da un sitio donde poner lógica de calidad legible |

### Consecuencias

- ~$0.0006 por ejecución: el Lab 2 completo cuesta céntimos.
- Migrar a PySpark si el dataset creciera es cambiar `command.name` a `glueetl` en `infra/lab2/glue.tf` y los DataFrames de pandas a Spark. La lógica de negocio no cambia.
- Con `--rows 500000` en el generador se empieza a notar el límite, y ese es el momento de hablar de Spark.

---

## ADR-011: Las reglas de calidad se miden en tres etapas

**Frase para la presentación:** *"Medir la calidad solo al final no enseña nada: todo sale en verde porque ya lo arreglaste. Hay que medir antes, en medio y después."*

| Campo | Valor |
|-------|-------|
| **Título** | Dónde se mide la calidad en el pipeline |
| **Fecha** | 2026-09-25 |
| **Estado** | Aceptado (revisa una implementación anterior que medía solo al final) |

### Contexto

La primera versión del job medía las 8 reglas después de transformar. Resultado: las reglas de `transaction_type` y `status` salían siempre en PASS, porque la normalización ya las había arreglado. El reporte nunca mostraba el problema que el lab promete enseñar.

### Decisión Tomada

`measure_rules(df)` se aplica tres veces: sobre `raw`, sobre el transformado, y sobre lo que va a `curated/`. El reporte publica las tres y atribuye cada arreglo.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Medir solo al final** | Es lo que había, y ocultaba justamente lo que hay que ver |
| **Medir solo antes y después** | No distingue qué arregló la normalización de qué se arregló apartando filas, que es la lección |
| **Glue Data Quality como servicio, con su ruleset** | Es lo correcto en producción, pero el resultado se ve en la consola y no en el reporte, así que en una demo se pierde. El ruleset DQDL se declara igualmente, como referencia. |

### Consecuencias

- El reporte muestra `raw 5/8 → transformado 7/8 → curated 8/8`, con `resolved_by` por regla.
- Las métricas de `curated` se miden sobre `valid_df`, no sobre el frame anterior al rechazo. Medir antes daba `uniqueness` en FAIL aunque los duplicados ya no llegaran a curated.
- `tests/test_glue_job.py` fija esa progresión: si alguien vuelve a medir en un solo punto, los tests fallan.

---

## ADR-012: Rechazar no es borrar

**Frase para la presentación:** *"Los registros que no pasan las reglas no se borran: se apartan a `rejected/` con el motivo. Un `DELETE` no es auditable."*

| Campo | Valor |
|-------|-------|
| **Título** | Qué hacer con los registros que fallan calidad |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

Hay dos formas de tratar un dato que falla una regla: arreglarlo o quitarlo. Y quitarlo puede significar borrarlo o apartarlo.

### Decisión Tomada

Dos caminos según si el dato es recuperable:

- **Recuperable → transformar.** `DEPÓSITO` es inequívocamente `DEPOSIT`; `completed` es `COMPLETED`. Se normaliza y la fila se conserva.
- **No recuperable → apartar.** Ante dos transacciones con el mismo `transaction_id`, decidir cuál es la buena es una decisión de negocio. El job no la inventa: manda **todas** las ocurrencias a `rejected/` con `rejection_reason`.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Descartar en silencio** | Los totales dejan de cuadrar y nadie sabe por qué. Imposible de auditar. |
| **Fallar el job entero** | Un dato malo entre mil no debería parar el pipeline |
| **Imputar los valores que faltan** | Inventarse un importe en datos financieros no es limpiar, es falsificar |
| **Dejar pasar todo con una marca** | Traslada el problema a cada consumidor, que lo resolverá distinto |

### Consecuencias

- `rejected/` crece con cada ejecución y es consultable: se puede preguntar por qué se rechazó algo.
- Los totales de `curated/` describen solo lo que pasó las reglas. Para conciliación hay que mirar también `rejected/`, y eso queda escrito en los documentos que genera `scripts/ingest_from_curated.py`.
- Una fila puede acumular varios motivos, y `rejection_reason` los concatena.

---

## ADR-013: Los datos curados llegan a la IA como hechos, no como filas

**Frase para la presentación:** *"Volcar 900 transacciones a un vector store no responde ninguna pregunta. Agregadas en frases sí."*

| Campo | Valor |
|-------|-------|
| **Título** | Cómo conectar el Lab 2 con el Lab 3 |
| **Fecha** | 2026-09-25 |
| **Estado** | Aceptado |

### Contexto

El workshop afirma que los datos curados alimentan a la IA. Durante un tiempo eso fue solo narrativo: el Lab 3 ingestaba documentos de compliance y no tocaba la salida del Lab 2.

### Decisión Tomada

`scripts/ingest_from_curated.py` lee el Parquet curado y el reporte de calidad, y genera tres documentos Markdown con **hechos agregados en lenguaje natural**: resumen mensual, calidad de los datos, y perfil de actividad. Esos documentos entran al vector store por el mismo pipeline que el resto del corpus.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Embeddear cada fila** | Nadie pregunta "dame TXN0000042". El embedding de una fila de CSV no responde a una pregunta de negocio, y 900 vectores casi idénticos degradan la búsqueda. |
| **Text-to-SQL sobre Athena** | Es la respuesta correcta para preguntas numéricas, y merece su propia charla. Aquí competiría con el RAG en vez de complementarlo. |
| **Dejarlo narrativo** | Es lo que había. Funciona para contarlo, pero el participante no ve la conexión. |

### Consecuencias

- El corpus pasa de 7 documentos a 10, y tres de ellos se regeneran con cada ejecución del Lab 2.
- El documento de calidad permite responder algo que ninguna consulta SQL contesta bien: *"¿puedo confiar en estos datos, y por qué?"*.
- Las cifras de los documentos derivados dependen del dataset. Si cambia, hay que regenerarlos.

---

## ADR-014: Terraform, no CloudFormation

**Frase para la presentación:** *"Un módulo por lab, con state independiente: si el Lab 1 se rompe, el Lab 3 sigue desplegable."*

| Campo | Valor |
|-------|-------|
| **Título** | Herramienta de infraestructura como código |
| **Fecha** | 2026-09-25 |
| **Estado** | Aceptado (sustituye a CloudFormation) |

### Contexto

Los tres labs empezaron en CloudFormation. Pasar el nombre del bucket del Lab 1 al Lab 2 obligaba a copiarlo a mano entre stacks y mantenerlo sincronizado.

### Decisión Tomada

Terraform, un módulo raíz por lab, más `infra/bootstrap` para el backend remoto.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Seguir en CloudFormation** | Los `Export`/`ImportValue` entre stacks crean acoplamiento: un stack que exporta un valor no se puede destruir mientras otro lo importe |
| **AWS CDK** | Añade Node o Python al toolchain y un paso de síntesis. Para infraestructura de lab, HCL declarativo se lee mejor en pantalla. |
| **Mantener ambos** | Dos definiciones de la misma infraestructura que divergen. Es el problema que ya tuvimos con `legacy/`. |

### Consecuencias

- El Lab 2 lee los outputs del Lab 1 con `terraform_remote_state`: si el Lab 1 cambia de bucket, el Lab 2 se entera solo.
- El state guarda las contraseñas **en claro**, así que va a S3 con `block_public_*` y nunca al repositorio.
- Hace falta un paso de bootstrap antes del primer lab. Es el precio del state remoto.

---

## ADR-015: State remoto en S3 con lock en DynamoDB

**Frase para la presentación:** *"El state describe toda tu infraestructura y guarda las contraseñas en claro. No vive en tu portátil."*

| Campo | Valor |
|-------|-------|
| **Título** | Dónde vive el state de Terraform |
| **Fecha** | 2026-09-25 |
| **Estado** | Aceptado |

### Contexto

Los labs se despliegan desde dos sitios: el portátil de quien los sigue y GitHub Actions.

### Decisión Tomada

Backend S3 con versionado y cifrado, más tabla DynamoDB para los locks. `infra/bootstrap` lo crea, y `infra/backend.hcl` (generado, no versionado) lo configura.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **State local** | Con GitHub Actions cada run empezaría de cero e intentaría recrear lo que ya existe. Y si se borra el fichero, los recursos quedan huérfanos. |
| **Terraform Cloud** | Gratis para equipos pequeños, pero añade una cuenta de terceros a un workshop que ya pide una de AWS |
| **Lock nativo de S3 (`use_lockfile`)** | Disponible desde Terraform 1.10 y más simple, pero obligaría a subir el mínimo desde 1.6 |

### Consecuencias

- El bucket del state tiene `prevent_destroy`: un `destroy` accidental sería irreversible para los otros tres módulos.
- `backend.hcl` lleva el account id, así que está en `.gitignore` y los workflows lo reconstruyen desde la identidad que asumen por OIDC.
- Sobrevive al cleanup a propósito. Cuesta céntimos al mes.

---

## ADR-016: Los labs son narrativamente progresivos, técnicamente independientes

**Frase para la presentación:** *"Si el Lab 1 falla en vivo, el Lab 3 arranca igual. La historia es progresiva; el acoplamiento, el mínimo."*

| Campo | Valor |
|-------|-------|
| **Título** | Grado de acoplamiento entre labs |
| **Fecha** | 2026-09-20 |
| **Estado** | Aceptado |

### Contexto

Los tres labs cuentan una historia: ingesta, calidad, IA. La tentación es encadenarlos técnicamente para que la historia sea literal. El riesgo es que un fallo en el primero deje la charla sin nada que mostrar.

### Decisión Tomada

Una sola dependencia técnica real: el Lab 2 lee el bucket del Lab 1. El Lab 3 tiene su propia infraestructura y arranca solo.

### Alternativas Consideradas

| Alternativa | Por qué no |
|-------------|-----------|
| **Encadenarlos por completo** | Un fallo en el Lab 1 tumba la charla entera |
| **Tres labs sin relación** | Se pierde la tesis: que la IA es la última etapa de una plataforma, no el punto de partida |

### Consecuencias

- El Lab 1 y el Lab 3 se pueden desplegar en paralelo, que es lo que hace el runbook para no esperar 35 minutos en serie.
- El Lab 2 tiene un fallback a `staging/` si `raw/` estuviera vacío.
- La conexión Lab 2 → Lab 3 es opcional: `scripts/ingest_from_curated.py` la hace técnica cuando se quiere (ADR-013).
