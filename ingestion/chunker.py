"""
Estrategia de chunking de documentos.

Por qué RecursiveCharacterTextSplitter (ver ADR-002):
Divide por párrafos primero, luego líneas, luego oraciones. Esto es crítico en
documentos de compliance donde una definición legal partida a la mitad puede
cambiar completamente el significado del chunk recuperado.

El overlap del 20% (200/1000) asegura que procedimientos que se extienden entre
dos chunks sean recuperables por cualquiera de los dos.
"""

from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rich.console import Console

from ingestion.config import chunking as chunk_cfg

console = Console()


def chunk_documents(documents: list[Document]) -> list[Document]:
    """
    Divide los documentos en chunks y añade metadata de índice para
    que la UI pueda mostrar "chunk 3 de 12" en el panel de retrieval.
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_cfg.chunk_size,
        chunk_overlap=chunk_cfg.chunk_overlap,
        separators=chunk_cfg.separators,
        # length_function mide en caracteres, no tokens.
        # Caracteres son suficientes para la estimación; tokens reales
        # dependen del modelo y añadirían latencia al pipeline de ingesta.
        length_function=len,
        is_separator_regex=False,
    )

    all_chunks: list[Document] = []

    for doc in documents:
        doc_chunks = splitter.split_documents([doc])

        # Añadir metadata de posición para que la UI pueda mostrar contexto
        total_chunks = len(doc_chunks)
        for idx, chunk in enumerate(doc_chunks):
            chunk.metadata["chunk_index"] = idx
            chunk.metadata["total_chunks"] = total_chunks
            # Preview del contenido para la UI (primeras 100 chars)
            chunk.metadata["content_preview"] = chunk.page_content[:100].replace("\n", " ")

        all_chunks.extend(doc_chunks)
        console.print(
            f"  [dim]{doc.metadata.get('filename', 'unknown')}[/dim] "
            f"→ {total_chunks} chunks"
        )

    console.print(
        f"[green]✓[/green] Total: {len(all_chunks)} chunks de {len(documents)} documentos"
    )
    return all_chunks
