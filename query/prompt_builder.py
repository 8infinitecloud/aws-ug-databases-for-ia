"""
Construcción del prompt final con las tres capas de memoria y los chunks recuperados.

El prompt es el producto final del pipeline RAG — aquí convergen:
  - Contexto de los documentos (RAG clásico)
  - Historial de la sesión actual (memoria de sesión)
  - Perfil del usuario (memoria persistente)
  - Conversaciones pasadas relevantes (memoria semántica)

El orden importa: ponemos el contexto más relevante más cerca del final del prompt
porque los LLMs tienden a prestar más atención al contenido reciente.
"""

from __future__ import annotations

from query.memory import MemoryContext
from query.retriever import RetrievedChunk


SYSTEM_PROMPT = """Eres el asistente interno de FinCorp, una empresa de servicios financieros.
Tu función es responder preguntas sobre compliance, onboarding de clientes y políticas internas.

Principios que debes seguir siempre:
1. Responde ÚNICAMENTE basándote en los documentos proporcionados. Si la información no está en el contexto, di explícitamente que no tienes esa información.
2. Cita la fuente específica al final de cada respuesta (nombre del documento y sección si está disponible).
3. Si la respuesta involucra un proceso de múltiples pasos, preséntala como una lista numerada.
4. Si hay consecuencias legales o regulatorias, resáltalas claramente.
5. Responde en el idioma de la pregunta (español o inglés).
6. Sé preciso y conciso — el personal de compliance valora la claridad sobre la extensividad.

NO inventes información, fechas, montos o procedimientos que no estén explícitamente en los documentos de contexto."""


def build_prompt(
    query: str,
    retrieved_chunks: list[RetrievedChunk],
    memory_context: MemoryContext,
) -> tuple[str, str]:
    """
    Construye el system prompt y el human message para Claude.

    Retorna (system_prompt, human_message) por separado porque la API de Claude
    separa el system prompt del mensaje del usuario — esta separación mejora
    la adherencia a las instrucciones del sistema.

    Estructura del human_message:
    1. Perfil del usuario (capa 2)
    2. Memorias semánticas relevantes (capa 3) — si las hay
    3. Historial de sesión reciente (capa 1)
    4. Contexto de documentos (RAG)
    5. Pregunta actual

    Ponemos la pregunta al final y el contexto RAG justo antes para que el LLM
    los relacione directamente (efecto de posición reciente).
    """
    sections: list[str] = []

    # ── Capa 2: Perfil del usuario ──────────────────────────────────────────
    profile = memory_context.user_profile
    if profile.name or profile.role or profile.department:
        user_info_parts = []
        if profile.name:
            user_info_parts.append(f"Nombre: {profile.name}")
        if profile.role:
            user_info_parts.append(f"Rol: {profile.role}")
        if profile.department:
            user_info_parts.append(f"Área: {profile.department}")
        if profile.session_count > 0:
            user_info_parts.append(f"Sesiones anteriores: {profile.session_count}")

        sections.append(
            "## Perfil del usuario\n" + "\n".join(user_info_parts)
        )

        if profile.summary:
            sections.append(
                f"## Resumen de interacciones anteriores\n{profile.summary}"
            )

    # ── Capa 3: Memorias semánticas ──────────────────────────────────────────
    if memory_context.semantic_memories:
        memories_text = "\n\n".join(
            f"- {mem}" for mem in memory_context.semantic_memories
        )
        sections.append(
            f"## Conversaciones pasadas relevantes\n{memories_text}"
        )

    # ── Capa 1: Historial de sesión ──────────────────────────────────────────
    if memory_context.session_messages:
        history_parts = []
        for msg in memory_context.session_messages[:-1]:  # excluir la pregunta actual
            role_label = "Usuario" if msg.role == "user" else "Asistente"
            history_parts.append(f"{role_label}: {msg.content}")
        if history_parts:
            sections.append(
                "## Historial de la conversación actual\n" + "\n".join(history_parts)
            )

    # ── Contexto RAG: chunks recuperados ────────────────────────────────────
    if retrieved_chunks:
        context_parts = []
        for i, chunk in enumerate(retrieved_chunks, 1):
            context_parts.append(
                f"[Fragmento {i} | Fuente: {chunk.source} | "
                f"Similitud: {chunk.score:.2f}]\n{chunk.content}"
            )
        sections.append(
            "## Información de los documentos de la empresa\n\n"
            + "\n\n---\n\n".join(context_parts)
        )
    else:
        sections.append(
            "## Información de los documentos\n"
            "No se encontraron fragmentos relevantes en la base de conocimiento."
        )

    # ── Pregunta actual ──────────────────────────────────────────────────────
    sections.append(f"## Pregunta\n{query}")

    human_message = "\n\n".join(sections)

    return SYSTEM_PROMPT, human_message


def format_sources(retrieved_chunks: list[RetrievedChunk]) -> list[dict]:
    """
    Formatea las fuentes para mostrar en la UI de Streamlit.
    Retorna lista de dicts con la info necesaria para el panel de fuentes.
    """
    seen_sources = set()
    sources = []

    for chunk in retrieved_chunks:
        if chunk.source not in seen_sources:
            seen_sources.add(chunk.source)
            sources.append(
                {
                    "filename": chunk.source,
                    "doc_type": chunk.doc_type,
                    "max_score": chunk.score,
                    "chunk_index": chunk.chunk_index,
                    "total_chunks": chunk.total_chunks,
                }
            )

    return sources
