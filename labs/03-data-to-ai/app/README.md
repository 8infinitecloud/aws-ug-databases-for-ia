# Aplicación Streamlit — Interfaz Visual del Lab

La UI es el corazón del laboratorio: muestra en tiempo real qué está pasando en cada capa del sistema RAG, haciendo visible lo que normalmente es invisible.

## Layout

```
┌────────────────┬────────────────┬────────────────────┐
│ 🔍 RETRIEVAL   │ 🧠 MEMORIA     │ 💬 RESPUESTA        │
│                │                │                    │
│ Top-5 chunks   │ Redis:         │ [Chat interface]   │
│ con scores     │  msgs sesión   │                    │
│                │                │ Respuesta Claude   │
│ Bar chart de   │ DynamoDB:      │                    │
│ similitudes    │  perfil user   │ Fuentes citadas:   │
│                │                │  • doc1 (0.92)     │
│ Detalle de     │ pgvector:      │  • doc2 (0.87)     │
│ cada chunk     │  memorias sem. │                    │
│ expandible     │                │ Token estimate     │
└────────────────┴────────────────┴────────────────────┘
```

## Decisiones de UI

**Tres columnas fijas:** La audiencia del lab debe ver las tres capas simultáneamente. Un layout de tabs ocultaría información clave — el punto del lab es mostrar todo junto.

**Plotly para el gráfico de scores:** Interactivo (hover para ver detalles), más impactante visualmente que `st.bar_chart`. La escala de color verde→amarillo→rojo hace inmediatamente obvio qué chunks son relevantes.

**st.expander para chunks:** La audiencia puede ver el contenido completo del chunk más relevante (expandido por defecto) sin sobrecargar la pantalla con todos los chunks.

**Sidebar para configuración:** Filtros de retrieval, perfil de usuario y preguntas de ejemplo en el sidebar mantienen el área principal limpia para el demo.

## Cómo extender la UI para producción

En producción, Streamlit no escala a múltiples usuarios simultáneos. Reemplazar con:
- **Backend:** FastAPI con endpoints REST para cada operación del pipeline
- **Frontend:** React + AWS Amplify para la interfaz
- **Auth:** Amazon Cognito para gestión de usuarios

## Inicio rápido

```bash
streamlit run app/app.py
# Abre: http://localhost:8501
```

Para demo en una presentación con proyector:
```bash
# Aumentar el zoom del navegador al 125% para mejor visibilidad
streamlit run app/app.py --server.port 8501 --browser.gatherUsageStats false
```
