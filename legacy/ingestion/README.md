# Pipeline de Ingesta

Convierte documentos Markdown en vectores almacenados en Aurora pgvector.

## Flujo

```
data/*.md → Loader → Chunker → Titan Embeddings V2 → pgvector
```

## Módulos

| Archivo | Qué hace | Decisión clave |
|---------|---------|----------------|
| `config.py` | Configuración centralizada desde `.env` | Singleton pattern — importar instancias, no clases |
| `loader.py` | Carga documentos desde disco o S3 | `UnstructuredMarkdownLoader` preserva headers como metadata |
| `chunker.py` | Divide en chunks con overlap | `RecursiveCharacterTextSplitter`: respeta párrafos legales |
| `embedder.py` | Genera embeddings con Titan V2 | Retry con backoff exponencial para throttling de Bedrock |
| `store.py` | Escribe en Aurora pgvector | `execute_values` para batch insert; contraseña via Secrets Manager |
| `pipeline.py` | Orquesta los 4 pasos | Punto de entrada del script `ingest_documents.py` |

## Por qué estas decisiones

**Chunking por caracteres, no por tokens:** Para el lab, la simplicidad importa. Dividir por tokens requiere instanciar el tokenizer de cada modelo, añade dependencia y latencia. Los chunks de 1000 caracteres (~250 tokens para español) están muy por debajo del límite de 8192 tokens de Titan V2.

**Overlap del 20%:** En documentos de compliance, las definiciones legales y los procedimientos multi-paso frecuentemente se extienden más allá del límite de un chunk. El overlap garantiza que ambos chunks vecinos contengan suficiente contexto para ser interpretados correctamente.

**Secrets Manager para la contraseña:** Nunca en variables de entorno en texto plano en producción. Para el lab local, `AURORA_PASSWORD` es un fallback documentado — pero el flow recomendado es siempre Secrets Manager.

## Alternativas consideradas

- **Chunking semántico** (LangChain `SemanticChunker`): mejor calidad pero requiere una llamada a Bedrock por chunk para determinar el boundary — demasiado lento para el lab.
- **Chunking por sección de Markdown**: correcto conceptualmente, pero frágil ante documentos mal formateados. `RecursiveCharacterTextSplitter` es más robusto.

## Uso

```bash
# Ingesta inicial
python scripts/ingest_documents.py --source data/

# Re-ingesta limpia (borra tabla primero)
python scripts/ingest_documents.py --source data/ --clear

# Desde S3
python scripts/ingest_documents.py --s3 --bucket mi-bucket --prefix docs/
```
