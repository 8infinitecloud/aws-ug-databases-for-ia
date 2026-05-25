"""
Búsqueda por similitud semántica en Aurora pgvector.

Retorna chunks con sus scores de similitud para visualización en la UI.
Los scores permiten a la audiencia ver concretamente qué tan relevante
es cada fragmento recuperado — el corazón visual del lab.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg2
from langchain_aws import BedrockEmbeddings

from ingestion.config import retrieval as retrieval_cfg
from ingestion.embedder import embed_query, get_embeddings_model
from ingestion.store import get_connection


@dataclass
class RetrievedChunk:
    """Chunk recuperado con toda la información necesaria para la UI."""
    content: str
    score: float          # similitud coseno [0.0, 1.0]; 1.0 = idéntico
    source: str           # nombre del archivo fuente
    doc_type: str         # compliance | onboarding | policy
    chunk_index: int      # posición del chunk en el documento original
    total_chunks: int     # total de chunks de ese documento
    content_preview: str  # primeros 100 chars para la UI


def similarity_search(
    query: str,
    top_k: int | None = None,
    min_score: float | None = None,
    doc_type_filter: str | None = None,
    embeddings_model: BedrockEmbeddings | None = None,
    query_embedding: list[float] | None = None,
) -> list[RetrievedChunk]:
    """
    Busca los chunks más similares a la query usando distancia coseno en pgvector.

    Por qué distancia coseno y no L2:
    Los embeddings de Titan V2 son normalizados (módulo=1), por lo que coseno y
    L2 dan el mismo ranking. Usamos coseno explícitamente (<=> en pgvector) porque
    el score resultante [0,1] es más interpretable para la UI que la distancia L2.

    Args:
        query: Pregunta del usuario en lenguaje natural
        top_k: Número de chunks a recuperar (default: config)
        min_score: Score mínimo aceptable (default: config)
        doc_type_filter: Filtrar solo por tipo de documento (optional)
        embeddings_model: Modelo de embeddings (crea uno si no se provee)

    Returns:
        Lista de RetrievedChunk ordenada por score descendente
    """
    k = top_k or retrieval_cfg.top_k
    min_s = min_score or retrieval_cfg.min_score

    if query_embedding is None:
        if embeddings_model is None:
            embeddings_model = get_embeddings_model()
        query_embedding = embed_query(query, embeddings_model)

    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    # La expresión (1 - (embedding <=> query)) convierte distancia coseno a similitud.
    # pgvector: <=> es distancia coseno [0,2]; 0 = idéntico, 2 = opuesto.
    # Similitud = 1 - distancia/2 → rango [0,1], 1 = idéntico.
    sql = """
        SELECT
            content,
            1 - (embedding <=> %(embedding)s::vector) / 2  AS similarity,
            metadata->>'filename'    AS source,
            metadata->>'doc_type'   AS doc_type,
            (metadata->>'chunk_index')::int                 AS chunk_index,
            (metadata->>'total_chunks')::int                AS total_chunks,
            metadata->>'content_preview'                    AS content_preview
        FROM document_chunks
        WHERE
            1 - (embedding <=> %(embedding)s::vector) / 2 >= %(min_score)s
            {type_filter}
        ORDER BY similarity DESC
        LIMIT %(top_k)s
    """

    type_filter_clause = (
        "AND metadata->>'doc_type' = %(doc_type)s" if doc_type_filter else ""
    )
    sql = sql.format(type_filter=type_filter_clause)

    params = {
        "embedding": embedding_str,
        "min_score": min_s,
        "top_k": k,
        "doc_type": doc_type_filter,
    }

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        RetrievedChunk(
            content=row[0],
            score=float(row[1]),
            source=row[2] or "desconocido",
            doc_type=row[3] or "general",
            chunk_index=row[4] or 0,
            total_chunks=row[5] or 1,
            content_preview=row[6] or row[0][:100],
        )
        for row in rows
    ]
