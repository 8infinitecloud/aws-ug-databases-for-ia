# Pipeline de Query (RAG + Memoria)

Convierte una pregunta en texto en una respuesta fundamentada en documentos, usando las tres capas de memoria.

## Flujo

```
Pregunta del usuario
    │
    ▼
Titan Embeddings V2 (embed_query)
    │
    ├──→ pgvector (similarity_search) → chunks + scores
    ├──→ Redis (get_session_messages) → historial de sesión
    ├──→ DynamoDB (get_user_profile) → perfil de usuario
    └──→ pgvector (search_semantic_memories) → conversaciones pasadas
    │
    ▼
prompt_builder.build_prompt(query, chunks, memory_context)
    │
    ▼
Claude Sonnet 3.5 via Bedrock → respuesta
    │
    ▼
Redis (add_session_message) → guardar en historial
```

## Módulos

| Archivo | Qué hace | Decisión clave |
|---------|---------|----------------|
| `retriever.py` | Búsqueda por similitud en pgvector | `1 - coseno/2` convierte distancia a similitud [0,1] |
| `memory.py` | Gestión de las 3 capas de memoria | Capa 3 se actualiza al CERRAR sesión, no en cada query |
| `prompt_builder.py` | Ensambla el prompt final | Contexto más reciente al final del prompt (efecto de posición) |
| `chain.py` | Orquesta todo el pipeline | `temperature=0` para determinismo en compliance |

## Las Tres Capas de Memoria

### Por qué tres servicios distintos

| Capa | Servicio | Porqué ese servicio |
|------|---------|---------------------|
| Sesión | Redis | RAM → latencia microsegundos; TTL nativo; no necesita persistencia |
| Usuario | DynamoDB | Acceso O(1) por user_id; sin TTL; schema flexible (JSONB-like) |
| Semántica | pgvector | Misma DB que RAG; búsqueda por similitud semántica; no por ID |

### Cuándo se actualiza cada capa

- **Redis:** Se actualiza en CADA query (add_session_message)
- **DynamoDB:** Se actualiza al GUARDAR sesión (increment_session_count) y al editar perfil
- **pgvector (memorias):** Se actualiza al CERRAR sesión (summarize_and_store_session)

Actualizar la memoria semántica en cada query generaría fragmentos demasiado cortos y redundantes. Es mejor un resumen semántico denso de la sesión completa.

## Por qué `temperature=0`

En aplicaciones de compliance, la reproducibilidad importa más que la creatividad. Con `temperature=0`, la misma pregunta con el mismo contexto siempre produce la misma respuesta — esencial para auditoría y para que el equipo de compliance confíe en el sistema.

## Alternativas consideradas

- **Bedrock ConversationHistory**: Feature managed de Bedrock para historial. Descartado porque abstrae la memoria de sesión y no permite mostrarla en la UI del lab.
- **LangChain ConversationBufferMemory**: Más simple, pero almacena en memoria del proceso (no persiste entre reinicios de la app, problema para demos en vivo).
