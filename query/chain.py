"""
Chain principal de RAG que orquesta retrieval + memoria + generación.

Este archivo es el "director de orquesta" del sistema. Lo que hace:
  1. Embed la query con Titan V2
  2. Busca chunks similares en pgvector
  3. Recopila las tres capas de memoria
  4. Construye el prompt
  5. Llama a Claude en Bedrock
  6. Persiste la respuesta en las capas de memoria correspondientes

Retorna datos enriquecidos (chunks, scores, estado de memoria) para que
la UI de Streamlit pueda visualizarlos en tiempo real.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import boto3
from langchain_aws import ChatBedrock
from langchain_core.messages import HumanMessage, SystemMessage

from ingestion.config import bedrock as bedrock_cfg
from ingestion.embedder import embed_query, get_embeddings_model
from query.memory import (
    MemoryContext,
    add_session_message,
    build_memory_context,
    store_conversation_memory,
)
from query.prompt_builder import build_prompt, format_sources
from query.retriever import RetrievedChunk, similarity_search


@dataclass
class RAGResponse:
    """Respuesta completa del pipeline, incluyendo metadata para la UI."""
    answer: str
    retrieved_chunks: list[RetrievedChunk]
    memory_context: MemoryContext
    sources: list[dict]
    prompt_token_estimate: int


def get_llm() -> ChatBedrock:
    """
    Retorna el cliente de Claude vía Bedrock.

    max_tokens=2048 es suficiente para respuestas de compliance detalladas
    sin exceder el límite de tokens de salida de Claude Sonnet.
    temperature=0 para respuestas deterministas — en compliance la reproducibilidad
    importa más que la creatividad.
    """
    bedrock_client = boto3.client(
        service_name="bedrock-runtime",
        region_name=bedrock_cfg.region,
    )

    return ChatBedrock(
        client=bedrock_client,
        model_id=bedrock_cfg.llm_model,
        model_kwargs={
            "max_tokens": 2048,
            "temperature": 0,      # determinista para compliance
            "top_p": 1,
        },
    )


def answer_question(
    query: str,
    user_id: str,
    session_id: str,
    doc_type_filter: str | None = None,
    top_k: int | None = None,
    min_score: float | None = None,
) -> RAGResponse:
    """
    Pipeline completo: query → retrieval → memoria → prompt → LLM → respuesta.

    Args:
        query: Pregunta del usuario
        user_id: ID del usuario (para DynamoDB y memoria semántica)
        session_id: ID de la sesión activa (para Redis)
        doc_type_filter: Filtrar retrieval por tipo de doc ("compliance", "onboarding", "policy")

    Returns:
        RAGResponse con respuesta y todos los datos para la UI
    """
    embeddings_model = get_embeddings_model()

    # Paso 1: Embed la query — mismo modelo que en la ingesta
    query_embedding = embed_query(query, embeddings_model)

    # Paso 2: Búsqueda por similitud en pgvector — reutiliza el embedding ya calculado
    retrieved_chunks = similarity_search(
        query=query,
        top_k=top_k,
        min_score=min_score,
        doc_type_filter=doc_type_filter,
        query_embedding=query_embedding,
    )

    # Paso 3: Recopilar las tres capas de memoria
    # Pasamos query_embedding para que la capa semántica pueda buscar
    # conversaciones pasadas similares a la pregunta actual.
    memory_context = build_memory_context(
        user_id=user_id,
        session_id=session_id,
        query_embedding=query_embedding,
    )

    # Paso 4: Construir el prompt con contexto + memoria
    system_prompt, human_message = build_prompt(
        query=query,
        retrieved_chunks=retrieved_chunks,
        memory_context=memory_context,
    )

    # Paso 5: Llamar a Claude
    llm = get_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=human_message),
    ]
    response = llm.invoke(messages)
    answer = response.content

    # Paso 6: Persistir en memoria de sesión (Capa 1)
    add_session_message(session_id, "user", query)
    add_session_message(session_id, "assistant", answer)

    # Nota: increment_session_count (Capa 2) se llama en app.py solo en la primera
    # query de la sesión — no aquí, para evitar incrementar en cada pregunta.

    # Nota: la memoria semántica (Capa 3) se actualiza al CERRAR la sesión,
    # no en cada query, para evitar fragmentos demasiado granulares.
    # Ver función `summarize_and_store_session()` abajo.

    sources = format_sources(retrieved_chunks)

    # Estimación de tokens consumidos (chars/4 es aproximación razonable para español)
    prompt_token_estimate = len(human_message) // 4 + len(system_prompt) // 4

    return RAGResponse(
        answer=answer,
        retrieved_chunks=retrieved_chunks,
        memory_context=memory_context,
        sources=sources,
        prompt_token_estimate=prompt_token_estimate,
    )


def summarize_and_store_session(
    session_id: str,
    user_id: str,
    session_messages: list[Any],
) -> None:
    """
    Al cerrar la sesión, genera un resumen con Claude y lo almacena en:
    - DynamoDB (Capa 2): resumen en texto para el perfil del usuario
    - pgvector (Capa 3): embedding del resumen para búsqueda semántica futura

    Esto evita que la memoria semántica acumule mensajes crudos y largos —
    trabajamos con resúmenes semánticos densos que capturan la esencia.
    """
    if not session_messages:
        return

    # Construir el historial de la sesión para el prompt de resumen
    history_text = "\n".join(
        f"{msg.role.upper()}: {msg.content}" for msg in session_messages
    )

    summary_prompt = f"""Resume la siguiente conversación en 2-3 oraciones concisas.
    Enfócate en: qué preguntó el usuario, qué información se le proporcionó, y cualquier preferencia o contexto relevante del usuario.

    Conversación:
    {history_text}

    Resumen:"""

    llm = get_llm()
    summary_response = llm.invoke([HumanMessage(content=summary_prompt)])
    summary = summary_response.content

    # Guardar resumen en DynamoDB como texto (Capa 2)
    from query.memory import UserProfile, update_user_profile, get_user_profile
    profile = get_user_profile(user_id)
    profile = UserProfile(
        **{
            **profile.__dict__,
            "summary": summary,
        }
    )
    update_user_profile(profile)

    # Guardar embedding del resumen en pgvector (Capa 3)
    embeddings_model = get_embeddings_model()
    summary_embedding = embed_query(summary, embeddings_model)
    store_conversation_memory(user_id, summary, summary_embedding)
