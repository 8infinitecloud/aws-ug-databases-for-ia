"""
Inicializa el schema de la base de datos PostgreSQL:
  - Extensión pgvector
  - Tabla document_chunks (vector store para RAG)
  - Tabla conversation_memories (memoria semántica — Capa 3)
  - Índices HNSW y GIN

Idempotente: seguro ejecutar múltiples veces (CREATE IF NOT EXISTS).
"""

import sys
from pathlib import Path

# Añadir el root del proyecto al path para los imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel

from ingestion.store import initialize_schema
from query.memory import initialize_memory_schema

console = Console()


def main() -> None:
    console.print(Panel.fit(
        "🗄️  Inicializando schema de Aurora PostgreSQL",
        subtitle="pgvector + tablas del lab",
    ))

    console.print("\n[1/2] Creando extensión vector y tabla document_chunks...")
    initialize_schema()

    console.print("\n[2/2] Creando tabla conversation_memories (Capa 3 — Memoria Semántica)...")
    initialize_memory_schema()

    console.print(Panel.fit(
        "[green]✅ Schema inicializado correctamente[/green]\n\n"
        "Próximo paso:\n"
        "[bold]python scripts/ingest_documents.py --source data/[/bold]",
        title="Listo",
    ))


if __name__ == "__main__":
    main()
