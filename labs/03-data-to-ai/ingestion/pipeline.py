"""
Orquestación del pipeline completo de ingesta.

Flujo: Cargar docs → Chunkear → Generar embeddings → Almacenar en pgvector.

Este archivo es el punto de entrada para el script de ingesta y también
puede usarse como referencia visual durante la presentación.
"""

from __future__ import annotations

from pathlib import Path

from rich.console import Console
from rich.rule import Rule

from ingestion.chunker import chunk_documents
from ingestion.embedder import embed_documents_batch, get_embeddings_model
from ingestion.loader import load_from_directory, load_from_s3
from ingestion.store import initialize_schema, store_chunks

console = Console()


def run_ingestion_pipeline(
    source: str | Path,
    use_s3: bool = False,
    s3_bucket: str = "",
    s3_prefix: str = "",
    clear_existing: bool = False,
) -> dict:
    """
    Ejecuta el pipeline completo de ingesta y retorna estadísticas.

    Args:
        source: Directorio local con los documentos
        use_s3: Si True, carga desde S3 en vez de directorio local
        s3_bucket: Nombre del bucket S3 (solo si use_s3=True)
        s3_prefix: Prefijo/carpeta dentro del bucket
        clear_existing: Si True, limpia la tabla antes de insertar

    Returns:
        dict con estadísticas: documentos cargados, chunks, tiempo
    """
    import time
    start_time = time.time()

    console.print(Rule("[bold blue]🔵 PASO 1: Carga de Documentos", style="blue"))

    if use_s3:
        documents = load_from_s3(s3_bucket, s3_prefix)
    else:
        documents = load_from_directory(source)

    console.print(f"\n[bold]Documentos cargados:[/bold] {len(documents)}")
    for doc in documents:
        console.print(
            f"  • {doc.metadata.get('filename', 'unknown')} "
            f"[dim]({doc.metadata.get('doc_type', 'general')})[/dim]"
        )

    console.print(Rule("[bold blue]🔵 PASO 2: Chunking", style="blue"))
    chunks = chunk_documents(documents)

    console.print(Rule("[bold blue]🔵 PASO 3: Generación de Embeddings (Titan V2)", style="blue"))
    embeddings_model = get_embeddings_model()
    chunks_with_embeddings = embed_documents_batch(chunks, embeddings_model)

    console.print(Rule("[bold blue]🔵 PASO 4: Almacenamiento en Aurora pgvector", style="blue"))
    initialize_schema()

    if clear_existing:
        from ingestion.store import clear_table
        clear_table()

    stored_count = store_chunks(chunks_with_embeddings)

    elapsed = time.time() - start_time

    stats = {
        "documents_loaded": len(documents),
        "chunks_created": len(chunks),
        "chunks_stored": stored_count,
        "elapsed_seconds": round(elapsed, 2),
    }

    console.print(Rule("[bold green]✅ Ingesta Completada", style="green"))
    console.print(f"  Documentos: {stats['documents_loaded']}")
    console.print(f"  Chunks:     {stats['chunks_created']}")
    console.print(f"  Almacenados:{stats['chunks_stored']}")
    console.print(f"  Tiempo:     {stats['elapsed_seconds']}s")

    return stats
