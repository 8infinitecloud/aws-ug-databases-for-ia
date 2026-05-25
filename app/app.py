"""
Streamlit app — Chat interface con observabilidad RAG on-demand.

Diseño: chat limpio como interfaz principal. Debajo de cada respuesta
del asistente hay un expander colapsado con todos los detalles internos
del pipeline (chunks, scores, memoria, tokens) para quien quiera verlos.
"""

import uuid

import streamlit as st
import plotly.graph_objects as go

from query.chain import RAGResponse, answer_question, summarize_and_store_session
from query.memory import (
    clear_session,
    get_session_messages,
    get_user_profile,
    increment_session_count,
    update_user_profile,
    UserProfile,
)

# ─────────────────────────────────────────────────────────────────────────────
# Personas de demo — nombre, rol y área ya vinculados
# ─────────────────────────────────────────────────────────────────────────────

DEMO_PERSONAS = [
    {
        "label": "Ana García · Asesora de Cuenta · Banca Personal",
        "user_id": "demo-ana-001",
        "name": "Ana García",
        "role": "Asesor de Cuenta",
        "department": "Banca Personal",
    },
    {
        "label": "Carlos Mendoza · Analista de Compliance · Compliance y Riesgo",
        "user_id": "demo-carlos-002",
        "name": "Carlos Mendoza",
        "role": "Analista de Compliance",
        "department": "Compliance y Riesgo",
    },
    {
        "label": "María Torres · Oficial AML · Compliance y Riesgo",
        "user_id": "demo-maria-003",
        "name": "María Torres",
        "role": "Oficial AML",
        "department": "Compliance y Riesgo",
    },
    {
        "label": "Roberto Silva · Gerente de Agencia · Banca Empresarial",
        "user_id": "demo-roberto-004",
        "name": "Roberto Silva",
        "role": "Gerente de Agencia",
        "department": "Banca Empresarial",
    },
]

EXAMPLE_QUESTIONS = {
    "Asesor de Cuenta": [
        "¿Qué documentos necesito para abrir una cuenta a un cliente nuevo?",
        "¿Cómo identifico si un cliente es de alto riesgo en el onboarding?",
        "¿Cuáles son los pasos para verificar la identidad de un cliente?",
    ],
    "Analista de Compliance": [
        "¿Cuáles son los pasos del proceso KYC para clientes corporativos?",
        "¿Cuál es el plazo de retención de documentos KYC?",
        "¿Qué son los beneficiarios finales (UBO) y cómo se identifican?",
    ],
    "Oficial AML": [
        "¿Cuándo es obligatorio hacer un Reporte de Actividad Sospechosa?",
        "¿Qué patrones de transacción indican posible lavado de dinero?",
        "¿Cuáles son los pasos de la debida diligencia reforzada (EDD)?",
    ],
    "Gerente de Agencia": [
        "¿Qué capacitaciones AML debe completar un empleado nuevo?",
        "¿Cuáles son mis responsabilidades en la política de privacidad de datos?",
        "¿Qué hacer si detecto una posible actividad sospechosa en mi agencia?",
    ],
}

# ─────────────────────────────────────────────────────────────────────────────
# Page config
# ─────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="FinCorp RAG Assistant",
    page_icon="🏦",
    layout="centered",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
    /* Quita padding superior innecesario */
    .block-container { padding-top: 1.5rem; }

    /* Score badges */
    .score-high { color: #1a7f37; font-weight: bold; }
    .score-med  { color: #9a6700; font-weight: bold; }
    .score-low  { color: #cf222e; font-weight: bold; }

    /* Chip de capa de memoria */
    .mem-chip {
        display: inline-block;
        padding: 2px 10px;
        border-radius: 12px;
        font-size: 0.78em;
        font-weight: 600;
        margin-bottom: 6px;
    }
    .chip-redis    { background:#ffd6d6; color:#8b0000; }
    .chip-dynamo   { background:#e8d6ff; color:#4b0082; }
    .chip-pgvector { background:#d6e8ff; color:#003080; }
</style>
""", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Session state
# ─────────────────────────────────────────────────────────────────────────────

if "session_id" not in st.session_state:
    st.session_state.session_id = str(uuid.uuid4())[:8]
if "user_id" not in st.session_state:
    st.session_state.user_id = DEMO_PERSONAS[0]["user_id"]
if "messages" not in st.session_state:
    st.session_state.messages = []   # [{role, content, rag_data?}]
if "total_queries" not in st.session_state:
    st.session_state.total_queries = 0
if "current_persona_label" not in st.session_state:
    st.session_state.current_persona_label = DEMO_PERSONAS[0]["label"]


# ─────────────────────────────────────────────────────────────────────────────
# Sidebar
# ─────────────────────────────────────────────────────────────────────────────

with st.sidebar:
    st.image(
        "https://upload.wikimedia.org/wikipedia/commons/9/93/Amazon_Web_Services_Logo.svg",
        width=110,
    )
    st.markdown("### FinCorp RAG Lab")
    st.caption("AWS Community Day · Databases for AI")

    st.divider()

    # ── Selector de persona demo ──────────────────────────────────────────────
    persona_labels = [p["label"] for p in DEMO_PERSONAS]
    selected_label = st.selectbox(
        "👤 Usuario",
        persona_labels,
        key="persona_selector",
    )
    selected_persona = next(p for p in DEMO_PERSONAS if p["label"] == selected_label)

    # Al cambiar de persona: reset sesión y auto-guardar perfil en DynamoDB
    if st.session_state.current_persona_label != selected_label:
        clear_session(st.session_state.session_id)
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.messages = []
        st.session_state.total_queries = 0
        st.session_state.current_persona_label = selected_label
        st.session_state.user_id = selected_persona["user_id"]
        update_user_profile(UserProfile(
            user_id=selected_persona["user_id"],
            name=selected_persona["name"],
            role=selected_persona["role"],
            department=selected_persona["department"],
        ))
        st.rerun()

    # Stats compactas
    profile = get_user_profile(st.session_state.user_id)
    c1, c2 = st.columns(2)
    c1.metric("Queries", st.session_state.total_queries)
    c2.metric("Sesiones", profile.session_count)

    st.divider()

    # Filtros retrieval
    with st.expander("🔍 Filtros de Retrieval", expanded=False):
        doc_type_filter = st.selectbox(
            "Tipo de documento",
            ["Todos", "compliance", "onboarding", "policy"],
        )
        doc_filter = None if doc_type_filter == "Todos" else doc_type_filter
        top_k = st.slider("Top-K chunks", 1, 10, 5)
        min_score = st.slider("Score mínimo", 0.0, 1.0, 0.70, 0.05)

    st.divider()

    # Control de sesión
    st.caption(f"Session: `{st.session_state.session_id}`")
    if st.button("🗑️ Nueva sesión", use_container_width=True):
        clear_session(st.session_state.session_id)
        st.session_state.session_id = str(uuid.uuid4())[:8]
        st.session_state.messages = []
        st.session_state.total_queries = 0
        st.rerun()

    if st.session_state.total_queries >= 4:
        if st.button("💾 Guardar sesión como memoria semántica", use_container_width=True,
                     help="Embeds el resumen de la sesión en pgvector para consultas futuras"):
            session_msgs = get_session_messages(st.session_state.session_id)
            if session_msgs:
                with st.spinner("Guardando en pgvector..."):
                    summarize_and_store_session(
                        session_id=st.session_state.session_id,
                        user_id=st.session_state.user_id,
                        session_messages=session_msgs,
                    )
                st.success("✓ Memoria semántica guardada")

    st.divider()

    # Preguntas de ejemplo filtradas por rol del usuario activo
    current_role = selected_persona["role"]
    role_questions = EXAMPLE_QUESTIONS.get(current_role, [])
    st.caption(f"**💡 Preguntas para {current_role}**")
    for q in role_questions:
        label = q if len(q) <= 52 else q[:50] + "…"
        if st.button(label, key=f"ex_{hash(q)}", use_container_width=True):
            st.session_state.pending_query = q


# ─────────────────────────────────────────────────────────────────────────────
# Header mínimo
# ─────────────────────────────────────────────────────────────────────────────

st.markdown(
    "<h3 style='margin-bottom:0'>🏦 FinCorp Compliance Assistant</h3>"
    "<p style='color:#888; font-size:0.85em; margin-top:2px'>"
    "Amazon Bedrock · pgvector · DynamoDB · Redis — AWS Community Day</p>",
    unsafe_allow_html=True,
)
st.divider()


# ─────────────────────────────────────────────────────────────────────────────
# Historial del chat
# ─────────────────────────────────────────────────────────────────────────────

for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        st.markdown(msg["content"])

        # Fuentes inline (solo asistente)
        if msg["role"] == "assistant" and msg.get("rag_data"):
            rd = msg["rag_data"]
            sources = rd.get("sources", [])
            if sources:
                src_text = " · ".join(
                    f"`{s['filename']}`" for s in sources
                )
                st.caption(f"📚 Fuentes: {src_text}")

        # Expander de observabilidad (solo asistente)
        if msg["role"] == "assistant" and msg.get("rag_data"):
            rd = msg["rag_data"]
            chunks = rd.get("chunks", [])
            semantic_mems = rd.get("semantic_memories", [])
            session_msgs = rd.get("session_messages", [])
            token_est = rd.get("token_estimate", 0)

            with st.expander("🔬 Ver internals del pipeline RAG"):
                tab_rag, tab_mem, tab_info = st.tabs(
                    ["🗃️ Vector Store", "🧠 Memoria", "📊 Info"]
                )

                # ── Tab 1: chunks recuperados ──────────────────────────────
                with tab_rag:
                    if chunks:
                        fig = go.Figure(go.Bar(
                            x=[c["score"] for c in chunks],
                            y=[f"#{i+1} {c['source'][:22]}" for i, c in enumerate(chunks)],
                            orientation="h",
                            marker=dict(
                                color=[c["score"] for c in chunks],
                                colorscale=[[0, "#ff6b6b"], [0.5, "#ffd93d"], [1, "#6bcb77"]],
                                cmin=0.6, cmax=1.0, showscale=False,
                            ),
                            text=[f"{c['score']:.3f}" for c in chunks],
                            textposition="outside",
                        ))
                        fig.update_layout(
                            height=180,
                            margin=dict(l=0, r=40, t=6, b=6),
                            xaxis=dict(range=[0, 1.1]),
                            yaxis=dict(autorange="reversed"),
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                        )
                        st.plotly_chart(fig, use_container_width=True)

                        for i, c in enumerate(chunks):
                            score_class = (
                                "score-high" if c["score"] >= 0.85
                                else "score-med" if c["score"] >= 0.70
                                else "score-low"
                            )
                            with st.expander(
                                f"Chunk #{i+1} · {c['source']} · score: {c['score']:.3f}"
                            ):
                                st.markdown(
                                    f"**Tipo:** {c['doc_type']} · "
                                    f"**Chunk:** {c['chunk_index']+1}/{c['total_chunks']}",
                                )
                                st.markdown(
                                    f"> {c['content'][:600]}"
                                    f"{'…' if len(c['content']) > 600 else ''}"
                                )
                    else:
                        st.warning("No se encontraron chunks con score suficiente.")

                # ── Tab 2: capas de memoria ────────────────────────────────
                with tab_mem:
                    st.markdown(
                        '<span class="mem-chip chip-redis">🔴 Capa 1 · Redis (sesión activa)</span>',
                        unsafe_allow_html=True,
                    )
                    if session_msgs:
                        for m in session_msgs[-4:]:
                            icon = "👤" if m["role"] == "user" else "🤖"
                            st.markdown(
                                f"{icon} {m['content'][:120]}"
                                f"{'…' if len(m['content']) > 120 else ''}"
                            )
                    else:
                        st.caption("Sin mensajes previos en la sesión.")

                    st.markdown("---")
                    st.markdown(
                        '<span class="mem-chip chip-dynamo">🟣 Capa 2 · DynamoDB (perfil persistente)</span>',
                        unsafe_allow_html=True,
                    )
                    p = rd.get("profile", {})
                    if p.get("name"):
                        st.markdown(
                            f"**{p.get('name')}** · {p.get('role', '')} · {p.get('department', '')} · "
                            f"{p.get('session_count', 0)} sesiones"
                        )
                        if p.get("summary"):
                            st.caption(p["summary"][:200])
                    else:
                        st.caption("Perfil vacío.")

                    st.markdown("---")
                    st.markdown(
                        '<span class="mem-chip chip-pgvector">🔵 Capa 3 · pgvector (memoria semántica)</span>',
                        unsafe_allow_html=True,
                    )
                    if semantic_mems:
                        for mem in semantic_mems:
                            st.markdown(f"💭 {mem[:180]}{'…' if len(mem) > 180 else ''}")
                    else:
                        st.caption("Sin memorias semánticas relevantes para esta consulta.")

                # ── Tab 3: info técnica ────────────────────────────────────
                with tab_info:
                    st.metric("Chunks recuperados", len(chunks))
                    st.metric("Tokens estimados en prompt", f"{token_est:,}")
                    st.metric("Mensajes en Redis", len(session_msgs))
                    if chunks:
                        st.metric("Score máximo", f"{max(c['score'] for c in chunks):.3f}")
                        st.metric("Score mínimo", f"{min(c['score'] for c in chunks):.3f}")


# ─────────────────────────────────────────────────────────────────────────────
# Input del usuario
# ─────────────────────────────────────────────────────────────────────────────

pending_query = st.session_state.pop("pending_query", None)
query = st.chat_input("Escribe tu pregunta sobre compliance, KYC o políticas…")
if pending_query:
    query = pending_query

if query:
    st.session_state.messages.append({"role": "user", "content": query})
    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Buscando documentos · leyendo memoria · generando respuesta…"):
            try:
                response: RAGResponse = answer_question(
                    query=query,
                    user_id=st.session_state.user_id,
                    session_id=st.session_state.session_id,
                    doc_type_filter=doc_filter,
                    top_k=top_k,
                    min_score=min_score,
                )

                if st.session_state.total_queries == 0:
                    increment_session_count(st.session_state.user_id)
                st.session_state.total_queries += 1

                st.markdown(response.answer)

                if response.sources:
                    src_text = " · ".join(f"`{s['filename']}`" for s in response.sources)
                    st.caption(f"📚 Fuentes: {src_text}")

                # Serializar datos observables (no guardar objetos complejos en session_state)
                current_profile = get_user_profile(st.session_state.user_id)
                session_msgs_raw = [
                    {"role": m.role, "content": m.content}
                    for m in get_session_messages(st.session_state.session_id)
                ]

                rag_data = {
                    "chunks": [
                        {
                            "score": c.score,
                            "source": c.source,
                            "content": c.content,
                            "doc_type": c.doc_type,
                            "chunk_index": c.chunk_index,
                            "total_chunks": c.total_chunks,
                        }
                        for c in response.retrieved_chunks
                    ],
                    "sources": response.sources,
                    "semantic_memories": response.memory_context.semantic_memories,
                    "session_messages": session_msgs_raw,
                    "profile": {
                        "name": current_profile.name,
                        "role": current_profile.role,
                        "department": current_profile.department,
                        "session_count": current_profile.session_count,
                        "summary": current_profile.summary,
                    },
                    "token_estimate": response.prompt_token_estimate,
                }

                st.session_state.messages.append({
                    "role": "assistant",
                    "content": response.answer,
                    "rag_data": rag_data,
                })

            except Exception as e:
                st.error(f"Error en el pipeline: {str(e)}")
                st.exception(e)
                st.session_state.messages.append({"role": "assistant", "content": f"❌ {str(e)}"})

    st.rerun()
