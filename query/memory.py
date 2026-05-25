"""
Gestión de las tres capas de memoria del asistente.

Las tres capas tienen propósitos, TTLs y costos distintos — por eso tres servicios:
  Capa 1 (Redis):    mensajes recientes de la sesión activa → contexto inmediato
  Capa 2 (DynamoDB): perfil del usuario y resúmenes de sesiones → contexto persistente
  Capa 3 (pgvector): embeddings de conversaciones pasadas → contexto semántico

Ver ADR-004 para el razonamiento completo de esta arquitectura.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import boto3
import redis as redis_lib

from ingestion.config import dynamodb as ddb_cfg
from ingestion.config import redis_cfg


# ─────────────────────────────────────────────────────────────────────────────
# Data classes para la UI
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SessionMessage:
    role: str       # "user" | "assistant"
    content: str
    timestamp: str


@dataclass
class UserProfile:
    user_id: str
    name: str = ""
    role: str = ""
    department: str = ""
    preferred_language: str = "es"
    session_count: int = 0
    last_seen: str = ""
    summary: str = ""          # resumen de sesiones anteriores generado por el LLM
    custom_prefs: dict = field(default_factory=dict)


@dataclass
class MemoryContext:
    """Todo el contexto de memoria listo para construir el prompt."""
    session_messages: list[SessionMessage]
    user_profile: UserProfile
    semantic_memories: list[str]   # fragmentos de conversaciones pasadas relevantes


# ─────────────────────────────────────────────────────────────────────────────
# CAPA 1: Memoria de Sesión (Redis)
# ─────────────────────────────────────────────────────────────────────────────

def get_redis_client() -> redis_lib.Redis:
    """Cliente Redis con connection pooling. Fail-fast si Redis no está disponible."""
    return redis_lib.Redis(
        host=redis_cfg.host,
        port=redis_cfg.port,
        decode_responses=True,
        socket_connect_timeout=3,
        socket_timeout=3,
    )


def get_session_messages(session_id: str) -> list[SessionMessage]:
    """
    Recupera los últimos N mensajes de la sesión desde Redis.

    La clave es session:{session_id}:messages — un Redis List donde el item más
    reciente está al inicio (LPUSH). LRANGE devuelve los últimos max_messages.
    """
    try:
        client = get_redis_client()
        key = f"session:{session_id}:messages"
        raw_messages = client.lrange(key, 0, redis_cfg.max_session_messages - 1)

        messages = []
        for raw in raw_messages:
            data = json.loads(raw)
            messages.append(
                SessionMessage(
                    role=data["role"],
                    content=data["content"],
                    timestamp=data.get("timestamp", ""),
                )
            )
        # Redis List tiene el más reciente primero; revertir para orden cronológico
        return list(reversed(messages))
    except redis_lib.RedisError:
        # Si Redis no está disponible, continuamos sin memoria de sesión.
        # En producción esto debería alertar, pero no romper la respuesta.
        return []


def add_session_message(session_id: str, role: str, content: str) -> None:
    """
    Añade un mensaje a la sesión en Redis con TTL automático.

    LPUSH + LTRIM mantiene la lista acotada sin necesitar un job de limpieza.
    El TTL (EXPIRE) garantiza que las sesiones antiguas se limpien solas.
    """
    try:
        client = get_redis_client()
        key = f"session:{session_id}:messages"
        message = json.dumps(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )
        client.lpush(key, message)
        client.ltrim(key, 0, redis_cfg.max_session_messages - 1)
        client.expire(key, redis_cfg.session_ttl_seconds)
    except redis_lib.RedisError:
        pass  # no-op si Redis no disponible — degradación elegante


def clear_session(session_id: str) -> None:
    """Limpia la sesión al cerrar — útil para el reset del lab."""
    try:
        client = get_redis_client()
        client.delete(f"session:{session_id}:messages")
    except redis_lib.RedisError:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# CAPA 2: Memoria Persistente de Usuario (DynamoDB)
# ─────────────────────────────────────────────────────────────────────────────

def get_dynamodb_table():
    ddb = boto3.resource("dynamodb", region_name=ddb_cfg.region)
    return ddb.Table(ddb_cfg.table_name)


def get_user_profile(user_id: str) -> UserProfile:
    """
    Recupera el perfil del usuario desde DynamoDB.

    Si el usuario no existe (primer acceso), retorna un perfil vacío.
    DynamoDB es perfecto aquí: acceso por clave primaria O(1), sin TTL,
    sin necesidad de query complejo.
    """
    try:
        table = get_dynamodb_table()
        response = table.get_item(Key={"user_id": user_id})
        item = response.get("Item")

        if not item:
            return UserProfile(user_id=user_id)

        return UserProfile(
            user_id=user_id,
            name=item.get("name", ""),
            role=item.get("role", ""),
            department=item.get("department", ""),
            preferred_language=item.get("preferred_language", "es"),
            session_count=int(item.get("session_count", 0)),
            last_seen=item.get("last_seen", ""),
            summary=item.get("summary", ""),
            custom_prefs=item.get("custom_prefs", {}),
        )
    except Exception:
        return UserProfile(user_id=user_id)


def update_user_profile(profile: UserProfile) -> None:
    """
    Actualiza el perfil del usuario en DynamoDB.

    UpdateExpression con SET es idempotente — si falla y se reintenta,
    no duplica datos. Importante para sistemas de alta disponibilidad.
    """
    try:
        table = get_dynamodb_table()
        table.update_item(
            Key={"user_id": profile.user_id},
            UpdateExpression="""
                SET #name = :name,
                    #role = :role,
                    department = :dept,
                    preferred_language = :lang,
                    session_count = :count,
                    last_seen = :last_seen,
                    summary = :summary,
                    custom_prefs = :prefs
            """,
            ExpressionAttributeNames={
                "#name": "name",    # 'name' es palabra reservada en DynamoDB
                "#role": "role",    # 'role' también es reservada
            },
            ExpressionAttributeValues={
                ":name": profile.name,
                ":role": profile.role,
                ":dept": profile.department,
                ":lang": profile.preferred_language,
                ":count": profile.session_count,
                ":last_seen": datetime.utcnow().isoformat(),
                ":summary": profile.summary,
                ":prefs": profile.custom_prefs,
            },
        )
    except Exception:
        pass


def increment_session_count(user_id: str) -> None:
    """Incrementa el contador de sesiones de forma atómica usando ADD."""
    try:
        table = get_dynamodb_table()
        table.update_item(
            Key={"user_id": user_id},
            UpdateExpression="ADD session_count :one SET last_seen = :now",
            ExpressionAttributeValues={
                ":one": 1,
                ":now": datetime.utcnow().isoformat(),
            },
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# CAPA 3: Memoria Semántica (pgvector — tabla separada)
# ─────────────────────────────────────────────────────────────────────────────

SEMANTIC_MEMORY_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS conversation_memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    summary     TEXT NOT NULL,
    embedding   vector(1024),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_user
    ON conversation_memories (user_id);

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON conversation_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 8, ef_construction = 32);
"""


def initialize_memory_schema() -> None:
    """Crea la tabla de memorias semánticas si no existe."""
    from ingestion.store import get_connection
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(SEMANTIC_MEMORY_TABLE_SQL)


def store_conversation_memory(
    user_id: str,
    summary: str,
    embedding: list[float],
) -> None:
    """
    Guarda el resumen de una conversación como memoria semántica en pgvector.

    La memoria semántica complementa DynamoDB: DynamoDB guarda el perfil estructurado
    del usuario; pgvector guarda el contenido semántico de conversaciones pasadas
    para recuperarlo por similitud cuando una nueva pregunta lo requiera.
    """
    from ingestion.store import get_connection
    import uuid

    with get_connection() as conn:
        with conn.cursor() as cur:
            embedding_str = "[" + ",".join(str(v) for v in embedding) + "]"
            cur.execute(
                """
                INSERT INTO conversation_memories (id, user_id, summary, embedding)
                VALUES (%s, %s, %s, %s::vector)
                """,
                (str(uuid.uuid4()), user_id, summary, embedding_str),
            )


def search_semantic_memories(
    user_id: str,
    query_embedding: list[float],
    top_k: int = 3,
    min_score: float = 0.65,
) -> list[str]:
    """
    Busca conversaciones pasadas del usuario que sean semánticamente similares
    a la pregunta actual.

    Se filtra por user_id primero (índice B-tree) para luego hacer la búsqueda
    vectorial solo sobre los registros de ese usuario — más eficiente y también
    garantiza privacidad (no cruzamos memorias de usuarios distintos).
    """
    from ingestion.store import get_connection

    embedding_str = "[" + ",".join(str(v) for v in query_embedding) + "]"

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    summary,
                    1 - (embedding <=> %(emb)s::vector) / 2 AS similarity
                FROM conversation_memories
                WHERE
                    user_id = %(user_id)s
                    AND 1 - (embedding <=> %(emb)s::vector) / 2 >= %(min_score)s
                ORDER BY similarity DESC
                LIMIT %(top_k)s
                """,
                {
                    "emb": embedding_str,
                    "user_id": user_id,
                    "min_score": min_score,
                    "top_k": top_k,
                },
            )
            rows = cur.fetchall()

    return [row[0] for row in rows]


# ─────────────────────────────────────────────────────────────────────────────
# Función principal: reúne las tres capas
# ─────────────────────────────────────────────────────────────────────────────

def build_memory_context(
    user_id: str,
    session_id: str,
    query_embedding: list[float] | None = None,
) -> MemoryContext:
    """
    Recopila el contexto de las tres capas de memoria.

    La capa semántica (pgvector) es opcional: si no se provee query_embedding,
    se omite. Esto permite usar build_memory_context en el inicio de sesión
    (cuando aún no hay query) sin error.
    """
    session_messages = get_session_messages(session_id)
    user_profile = get_user_profile(user_id)

    semantic_memories: list[str] = []
    if query_embedding is not None:
        semantic_memories = search_semantic_memories(user_id, query_embedding)

    return MemoryContext(
        session_messages=session_messages,
        user_profile=user_profile,
        semantic_memories=semantic_memories,
    )
