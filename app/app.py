"""
Aplicación Streamlit — Interfaz visual del laboratorio RAG.

Layout de tres columnas que muestra en tiempo real:
  Columna 1 (izquierda): Chunks recuperados con scores de similitud
  Columna 2 (centro):    Las tres capas de memoria activas
  Columna 3 (derecha):   Respuesta final con fuentes citadas

El objetivo es que la audiencia vea exactamente qué información
alimenta el LLM en cada query — haciendo visible lo que normalmente
es invisible en un sistema RAG.
"""

import uuid
from datetime import datetime

import streamlit as st
import plotly.graph_objects as go

from query.chain import RAGResponse, answer_question, summarize_and_store_session
from query.memory import (
    SessionMessage,
    build_memory_context,
    clear_session,
    get_session_messages,
    get_user_profile,
    increment_session_count,
    update_user_profile,
    UserProfile,
)
from query.retriever import RetrievedChunk

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="FinCorp RAG Lab — AWS Community",
    page_icon="🏦",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─────────────────────────────────────────────────────────────────────────────
# Custom CSS — apariencia limpia para la presentación
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<style>
    /* Panel headers */
    .panel-header {
        background: linear-gradient(90deg, #232f3e, #ff9900);
        color: white;
        padding: 8px 16px;
        border-radius: 8px;
        font-weight: bold;
        margin-bottom: 12px;
    }
    /* Score badge */
    .score-badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 0.85em;
        font-weight: bold;
    }
    .score-high { background-color: #d4edda; color: #155724; }
    .score-med  { background-color: #fff3cd; color: #856404; }
    .score-low  { background-color: #f8d7da; color: #721c24; }
    /* Chunk card */
    .chunk-card {
        border-left: 4px solid #ff9900;
        padding: 8px 12px;
        margin: 8px 0;
        background: #f8f9fa;
        border-radius: 0 8px 8px 0;
        font-size: 0.9em;
    }
    /* Memory layer badge */
    .memory-layer {
        border: 2px solid;
        border-radius: 8px;
        padding: 8px;
        margin: 8px 0;
    }
    .layer-redis    { border-color: #dc3545; background: #fff5f5; }
    .layer-dynamo   { border-color: #6f42c1; background: #f8f5ff; }
    .layer-pgvector { border-color: #0d6efd; background: #f0f5ff; }
    /* Token counter */
    .token-counter {
        font-size: 0.8em;
        color: #6c757d;
        text-align: right;
    }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Session state initialization
# ─────────────────────────────────────────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]

if "user_id" not in st.session_state:
    st.session_state.user_id = "demo-user-001"

if "messages" not in st.session_state:
    st.session_state.messages = []

if "last_response" not in st.session_state:
    st.session_state.last_response = None

if "total_queries" not in st.session_state:
    st.session_state.total_queries = 0


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar — Configuración del lab
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/9/93/Amazon_Web_Services_Logo.svg",
        width=120,
    )
    st.title("⚙️ Configuración del Lab")
    st.caption("AWS Community Day | RAG + Memoria")

    st.divider()

    # Perfil del usuario (Capa 2 — DynamoDB)
    st.subheader("👤 Perfil de Usuario (DynamoDB)")
    user_name = st.text_input("Nombre", value="Ana García", key="user_name_input")
    user_role = st.selectbox(
        "Rol",
        ["Asesor de Cuenta", "Analista de Compliance", "Oficial AML", "Gerente de Agencia"],
    )
    user_dept = st.selectbox(
        "Área",
        ["Banca Personal", "Banca Empresarial", "Compliance y Riesgo", "Operaciones"],
    )

    if st.button("💾 Guardar perfil en DynamoDB", use_container_width=True):
        profile = UserProfile(
            user_id=st.session_state.user_id,
            name=user_name,
            role=user_role,
            department=user_dept,
        )
        update_user_profile(profile)
        st.success("✓ Perfil guardado en DynamoDB")

    st.divider()

    # Filtros de retrieval
    st.subheader("🔍 Filtros de Retrieval")
    doc_type_filter = st.selectbox(
        "Tipo de documento",
        ["Todos", "compliance", "onboarding", "policy"],
    )
    doc_filter = None if doc_type_filter == "Todos" else doc_type_filter

    top_k = st.slider("Chunks a recuperar (Top-K)", min_value=1, max_value=10, value=5)
    min_score = st.slider(
        "Score mínimo de similitud", min_value=0.0, max_value=1.0, value=0.70, step=0.05
    )

    st.divider()

    # Control de sesión
    st.subheader("🔄 Control de Sesión")
    st.caption(f"Session ID: `{st.session_state.session_id}`")
    st.caption(f"User ID: `{st.session_state.user_id}`")

    if st.button("🗑️ Nueva sesión (limpiar Redis)", use_container_width=True):
        clear_session(st.session_state.session_id)
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.messages = []
        st.session_state.last_response = None
        st.session_state.total_queries = 0
        st.rerun()

    st.divider()

    # Preguntas de ejemplo
    st.subheader("💡 Preguntas de Ejemplo")
    example_questions = [
        "¿Cuáles son los pasos del proceso KYC para clientes corporativos?",
        "¿Qué documentos necesito para onboarding de cliente de alto riesgo?",
        "¿Cuándo es obligatorio hacer un Reporte de Actividad Sospechosa?",
        "¿Cuál es el plazo de retención de documentos KYC?",
        "¿Qué capacitaciones AML debe completar un empleado nuevo?",
        "¿Qué son los beneficiarios finales (UBO) y cómo se identifican?",
    ]
    for q in example_questions:
        if st.button(q[:55] + "...", key=f"ex_{hash(q)}", use_container_width=True):
            st.session_state.pending_query = q


# ─────────────────────────────────────────────────────────────────────────────
# Header principal
# ─────────────────────────────────────────────────────────────────────────────

st.markdown("""
<div style="background: linear-gradient(135deg, #232f3e 0%, #1a1a2e 100%);
            padding: 20px; border-radius: 12px; margin-bottom: 20px;">
    <h1 style="color: #ff9900; margin: 0;">🏦 FinCorp Compliance Assistant</h1>
    <p style="color: #adb5bd; margin: 4px 0 0 0;">
        Demo en vivo | Amazon Bedrock + pgvector + DynamoDB + Redis | AWS Community Day
    </p>
</div>
""", unsafe_allow_html=True)

# Stats rápidas en la parte superior
col_s1, col_s2, col_s3, col_s4 = st.columns(4)
with col_s1:
    st.metric("Queries en sesión", st.session_state.total_queries)
with col_s2:
    msgs = get_session_messages(st.session_state.session_id)
    st.metric("Mensajes en Redis", len(msgs))
with col_s3:
    profile = get_user_profile(st.session_state.user_id)
    st.metric("Sesiones históricas", profile.session_count)
with col_s4:
    st.metric("Session ID", st.session_state.session_id)

st.divider()

# ─────────────────────────────────────────────────────────────────────────────
# Layout principal: 3 columnas
# ─────────────────────────────────────────────────────────────────────────────

col_retrieval, col_memory, col_response = st.columns([1, 1, 1.2])

# ── Columna 1: Panel de Retrieval ─────────────────────────────────────────────

with col_retrieval:
    st.markdown(
        '<div class="panel-header">🔍 CAPA RAG — Vector Store (pgvector)</div>',
        unsafe_allow_html=True,
    )

    if st.session_state.last_response:
        resp: RAGResponse = st.session_state.last_response
        chunks = resp.retrieved_chunks

        st.caption(f"**{len(chunks)} chunks recuperados** | Top-{top_k} por similitud coseno")

        if chunks:
            # Gráfico de barras de scores — visualmente impactante en el demo
            fig = go.Figure(
                go.Bar(
                    x=[c.score for c in chunks],
                    y=[f"#{i+1} {c.source[:20]}" for i, c in enumerate(chunks)],
                    orientation="h",
                    marker=dict(
                        color=[c.score for c in chunks],
                        colorscale=[[0, "#ff6b6b"], [0.5, "#ffd93d"], [1, "#6bcb77"]],
                        cmin=0.6,
                        cmax=1.0,
                        showscale=False,
                    ),
                    text=[f"{c.score:.3f}" for c in chunks],
                    textposition="outside",
                )
            )
            fig.update_layout(
                height=200,
                margin=dict(l=0, r=40, t=10, b=10),
                xaxis=dict(range=[0, 1.1], title="Similitud Coseno"),
                yaxis=dict(autorange="reversed"),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
            )
            st.plotly_chart(fig, use_container_width=True)

            # Detalle de cada chunk
            for i, chunk in enumerate(chunks):
                score_class = (
                    "score-high" if chunk.score >= 0.85
                    else "score-med" if chunk.score >= 0.70
                    else "score-low"
                )
                with st.expander(
                    f"📄 Chunk #{i+1} | {chunk.source} | "
                    f"Score: {chunk.score:.3f}",
                    expanded=(i == 0),
                ):
                    st.markdown(
                        f'<span class="score-badge {score_class}">Score: {chunk.score:.3f}</span> '
                        f'<span style="color: #6c757d; font-size: 0.85em;">'
                        f'Tipo: {chunk.doc_type} | Chunk {chunk.chunk_index+1}/{chunk.total_chunks}'
                        f"</span>",
                        unsafe_allow_html=True,
                    )
                    st.markdown(
                        f'<div class="chunk-card">{chunk.content[:500]}{"..." if len(chunk.content) > 500 else ""}</div>',
                        unsafe_allow_html=True,
                    )
        else:
            st.warning("⚠️ No se encontraron chunks con score suficiente. Intenta bajar el score mínimo en la barra lateral.")
    else:
        st.info("👆 Realiza una consulta para ver los chunks recuperados del vector store.")
        st.markdown("""
        **¿Qué verás aquí?**
        - Los fragmentos de documentos más similares a tu pregunta
        - El score de similitud coseno [0-1] de cada fragmento
        - La fuente exacta (nombre del archivo y posición del chunk)
        """)


# ── Columna 2: Panel de Memoria ────────────────────────────────────────────────

with col_memory:
    st.markdown(
        '<div class="panel-header">🧠 TRES CAPAS DE MEMORIA</div>',
        unsafe_allow_html=True,
    )

    # Capa 1: Redis
    st.markdown(
        '<div class="memory-layer layer-redis">'
        '<strong>🔴 Capa 1: Sesión Activa (Redis)</strong><br>'
        '<small>TTL: 2 horas | En memoria RAM</small>'
        "</div>",
        unsafe_allow_html=True,
    )

    session_messages = get_session_messages(st.session_state.session_id)
    if session_messages:
        for msg in session_messages[-4:]:  # últimos 4 para no saturar la UI
            icon = "👤" if msg.role == "user" else "🤖"
            bg = "#fff5f5" if msg.role == "user" else "#f5f5ff"
            st.markdown(
                f'<div style="background:{bg}; padding:6px 10px; '
                f'border-radius:6px; margin:4px 0; font-size:0.85em;">'
                f"{icon} {msg.content[:120]}{'...' if len(msg.content) > 120 else ''}"
                "</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("*Sin mensajes en la sesión actual*")

    st.divider()

    # Capa 2: DynamoDB
    st.markdown(
        '<div class="memory-layer layer-dynamo">'
        '<strong>🟣 Capa 2: Perfil de Usuario (DynamoDB)</strong><br>'
        '<small>Persistente | Sin TTL</small>'
        "</div>",
        unsafe_allow_html=True,
    )

    current_profile = get_user_profile(st.session_state.user_id)
    if current_profile.name or current_profile.role:
        col_p1, col_p2 = st.columns(2)
        with col_p1:
            if current_profile.name:
                st.markdown(f"**👤** {current_profile.name}")
            if current_profile.role:
                st.markdown(f"**💼** {current_profile.role}")
        with col_p2:
            if current_profile.department:
                st.markdown(f"**🏢** {current_profile.department}")
            st.markdown(f"**📊** {current_profile.session_count} sesiones")

        if current_profile.summary:
            with st.expander("📝 Resumen de sesiones anteriores"):
                st.markdown(current_profile.summary)
    else:
        st.caption("*Perfil vacío — completa el formulario en la barra lateral*")

    st.divider()

    # Capa 3: pgvector (memoria semántica)
    st.markdown(
        '<div class="memory-layer layer-pgvector">'
        '<strong>🔵 Capa 3: Memoria Semántica (pgvector)</strong><br>'
        '<small>Embeddings de conversaciones pasadas</small>'
        "</div>",
        unsafe_allow_html=True,
    )

    if st.session_state.last_response:
        semantic_mems = st.session_state.last_response.memory_context.semantic_memories
        if semantic_mems:
            st.caption(f"**{len(semantic_mems)} memorias semánticas relevantes encontradas:**")
            for i, mem in enumerate(semantic_mems):
                st.markdown(
                    f'<div style="background:#f0f5ff; padding:6px 10px; '
                    f'border-radius:6px; margin:4px 0; font-size:0.85em;">'
                    f"💭 {mem[:150]}{'...' if len(mem) > 150 else ''}"
                    "</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.caption("*Sin memorias semánticas relevantes para esta consulta*")
            st.caption("*(Las memorias se generan al cerrar sesiones anteriores)*")
    else:
        st.caption("*Realiza una consulta para ver las memorias semánticas activas*")

    # Token estimate
    if st.session_state.last_response:
        st.divider()
        token_est = st.session_state.last_response.prompt_token_estimate
        st.markdown(
            f'<div class="token-counter">~{token_est:,} tokens en el prompt</div>',
            unsafe_allow_html=True,
        )


# ── Columna 3: Chat + Respuesta ────────────────────────────────────────────────

with col_response:
    st.markdown(
        '<div class="panel-header">💬 RESPUESTA — Claude via Amazon Bedrock</div>',
        unsafe_allow_html=True,
    )

    # Historial del chat
    chat_container = st.container()
    with chat_container:
        for msg in st.session_state.messages:
            with st.chat_message(msg["role"]):
                st.markdown(msg["content"])

    # Input del usuario (también acepta pregunta desde ejemplos del sidebar)
    pending_query = st.session_state.pop("pending_query", None)
    query = st.chat_input("Escribe tu pregunta sobre compliance, KYC o políticas...")

    if pending_query:
        query = pending_query

    if query:
        # Mostrar pregunta del usuario en el chat
        st.session_state.messages.append({"role": "user", "content": query})
        with st.chat_message("user"):
            st.markdown(query)

        # Llamar al pipeline RAG con spinner visible
        with st.chat_message("assistant"):
            with st.spinner("🔍 Buscando en documentos → 🧠 Leyendo memoria → 🤖 Generando respuesta..."):
                try:
                    response = answer_question(
                        query=query,
                        user_id=st.session_state.user_id,
                        session_id=st.session_state.session_id,
                        doc_type_filter=doc_filter,
                        top_k=top_k,
                        min_score=min_score,
                    )

                    # Incrementar sesiones solo en la primera pregunta de cada sesión
                    if st.session_state.total_queries == 0:
                        increment_session_count(st.session_state.user_id)

                    st.session_state.last_response = response
                    st.session_state.total_queries += 1

                    # Mostrar respuesta
                    st.markdown(response.answer)

                    # Fuentes citadas
                    if response.sources:
                        st.divider()
                        st.caption("📚 **Fuentes consultadas:**")
                        for src in response.sources:
                            icon = {
                                "compliance": "⚖️",
                                "onboarding": "👋",
                                "policy": "📋",
                            }.get(src["doc_type"], "📄")
                            st.markdown(
                                f'{icon} `{src["filename"]}` — '
                                f'Score máx: **{src["max_score"]:.3f}**'
                            )

                    st.session_state.messages.append(
                        {"role": "assistant", "content": response.answer}
                    )

                except Exception as e:
                    error_msg = f"Error en el pipeline RAG: {str(e)}"
                    st.error(error_msg)
                    st.exception(e)

        # Rerun para actualizar los paneles de retrieval y memoria
        st.rerun()

    # Opción para guardar sesión como memoria semántica
    if st.session_state.messages and len(st.session_state.messages) >= 4:
        if st.button(
            "💾 Guardar sesión como memoria semántica (Capa 3)",
            use_container_width=True,
            help="Genera un resumen de la sesión y lo guarda como embedding en pgvector",
        ):
            session_msgs = get_session_messages(st.session_state.session_id)
            if session_msgs:
                with st.spinner("Generando resumen y guardando en pgvector..."):
                    summarize_and_store_session(
                        session_id=st.session_state.session_id,
                        user_id=st.session_state.user_id,
                        session_messages=session_msgs,
                    )
                st.success(
                    "✓ Sesión guardada como memoria semántica en pgvector. "
                    "Aparecerá en la Capa 3 en consultas futuras similares."
                )


# ─────────────────────────────────────────────────────────────────────────────
# Footer
# ─────────────────────────────────────────────────────────────────────────────

st.divider()
st.markdown(
    '<div style="text-align: center; color: #6c757d; font-size: 0.85em;">'
    "🏦 FinCorp Compliance Assistant | "
    "Amazon Bedrock (Claude Sonnet 3.5 + Titan Embeddings V2) | "
    "Aurora pgvector + DynamoDB + ElastiCache Redis | "
    "AWS Community Day Lab"
    "</div>",
    unsafe_allow_html=True,
)
