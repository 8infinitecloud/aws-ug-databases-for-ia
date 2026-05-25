"""
Almacenamiento de chunks y embeddings en Aurora PostgreSQL con pgvector.

Por qué pgvector en Aurora (ver ADR-001): SQL familiar, joins con datos
relacionales posibles, serverless escala a cero. Para el lab, la latencia
de ~50ms por búsqueda es perfectamente aceptable.

Schema diseñado para mostrar en la UI: la columna metadata JSONB permite
filtrar por doc_type, filename, etc. sin columnas adicionales.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from typing import Generator

import boto3
import psycopg2
from psycopg2.extras import execute_values, Json
from langchain_core.documents import Document
from rich.console import Console

from ingestion.config import aurora as aurora_cfg

console = Console()

SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS document_chunks (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content     TEXT NOT NULL,
    embedding   vector(1024),          -- dimensión de Titan V2; cambiar si se usa 256 o 512
    metadata    JSONB NOT NULL DEFAULT '{}',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Índice HNSW: mejor balance velocidad/precisión para corpus <1M docs.
-- Para >1M docs, considerar IVFFlat con nlist=sqrt(total_rows).
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
    ON document_chunks
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Índice GIN en metadata para filtrado eficiente por doc_type, filename, etc.
CREATE INDEX IF NOT EXISTS idx_chunks_metadata
    ON document_chunks
    USING gin (metadata);
"""


def _get_password() -> str:
    """
    Obtiene la contraseña de Aurora desde Secrets Manager.

    En producción NUNCA usar contraseñas hardcodeadas ni en variables de entorno
    en texto plano. Secrets Manager también rota las credenciales automáticamente.

    Para desarrollo local (sin Secrets Manager), se puede usar AURORA_PASSWORD.
    """
    if aurora_cfg.password_override:
        return aurora_cfg.password_override

    if not aurora_cfg.secret_arn:
        raise ValueError(
            "Configura AURORA_SECRET_ARN o AURORA_PASSWORD en .env\n"
            "Para desarrollo local: AURORA_PASSWORD=tu_password"
        )

    sm_client = boto3.client("secretsmanager", region_name="us-east-1")
    secret = sm_client.get_secret_value(SecretId=aurora_cfg.secret_arn)
    secret_data = json.loads(secret["SecretString"])
    return secret_data["password"]


@contextmanager
def get_connection() -> Generator[psycopg2.extensions.connection, None, None]:
    """Context manager para conexiones a Aurora. Siempre cierra la conexión."""
    conn = psycopg2.connect(
        host=aurora_cfg.host,
        port=aurora_cfg.port,
        dbname=aurora_cfg.database,
        user=aurora_cfg.user,
        password=_get_password(),
        connect_timeout=10,
        # sslmode=require es obligatorio en Aurora — la conexión falla sin SSL
        sslmode="require",
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_schema() -> None:
    """Crea la tabla y los índices si no existen. Idempotente: safe para ejecutar múltiples veces."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SCHEMA_SQL)
    console.print("[green]✓[/green] Schema inicializado (extensión vector, tabla, índices)")


def store_chunks(chunks_with_embeddings: list[tuple[Document, list[float]]]) -> int:
    """
    Inserta chunks con sus embeddings en pgvector.

    Usa execute_values para inserción en batch — mucho más eficiente que
    INSERT individual en un loop (reduce roundtrips a la DB de N a 1).

    Retorna el número de chunks insertados.
    """
    if not chunks_with_embeddings:
        return 0

    rows = [
        (
            str(uuid.uuid4()),
            doc.page_content,
            # pgvector espera el embedding como string "[0.1, 0.2, ...]"
            "[" + ",".join(str(v) for v in embedding) + "]",
            Json(doc.metadata),
        )
        for doc, embedding in chunks_with_embeddings
    ]

    with get_connection() as conn:
        with conn.cursor() as cur:
            execute_values(
                cur,
                """
                INSERT INTO document_chunks (id, content, embedding, metadata)
                VALUES %s
                ON CONFLICT (id) DO NOTHING
                """,
                rows,
                template="(%s, %s, %s::vector, %s)",
            )

    console.print(f"[green]✓[/green] {len(rows)} chunks almacenados en pgvector")
    return len(rows)


def clear_table() -> None:
    """Limpia la tabla para re-ingestar. Úsalo en el lab para reset rápido."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"TRUNCATE TABLE {aurora_cfg.table_name}")
    console.print("[yellow]⚠[/yellow] Tabla vaciada — lista para re-ingesta")
