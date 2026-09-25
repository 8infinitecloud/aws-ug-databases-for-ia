"""
Script de ingesta de documentos para el laboratorio RAG.

Uso:
    python scripts/ingest_documents.py --source data/
    python scripts/ingest_documents.py --source data/ --clear  # re-ingesta limpia
    python scripts/ingest_documents.py --s3 --bucket mi-bucket --prefix compliance-docs/
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from rich.console import Console
from rich.panel import Panel

from ingestion.pipeline import run_ingestion_pipeline

console = Console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ingesta documentos en Aurora pgvector para el lab RAG"
    )
    parser.add_argument(
        "--source",
        default="data/",
        help="Directorio local con los documentos (default: data/)",
    )
    parser.add_argument(
        "--clear",
        action="store_true",
        help="Limpiar la tabla antes de ingestar (re-ingesta completa)",
    )
    parser.add_argument(
        "--s3",
        action="store_true",
        help="Cargar desde S3 en vez de directorio local",
    )
    parser.add_argument(
        "--bucket",
        default="",
        help="Nombre del bucket S3 (solo con --s3)",
    )
    parser.add_argument(
        "--prefix",
        default="compliance-docs/",
        help="Prefijo S3 (solo con --s3)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    console.print(Panel.fit(
        "📥 Pipeline de Ingesta — FinCorp Compliance RAG Lab",
        subtitle="Amazon Bedrock Titan Embeddings V2 → Aurora pgvector",
    ))

    if args.clear:
        console.print("[yellow]⚠️  Modo --clear: la tabla se vaciará antes de ingestar[/yellow]")

    stats = run_ingestion_pipeline(
        source=args.source,
        use_s3=args.s3,
        s3_bucket=args.bucket,
        s3_prefix=args.prefix,
        clear_existing=args.clear,
    )

    console.print(Panel.fit(
        f"[green]✅ Ingesta completada[/green]\n\n"
        f"  Documentos procesados: [bold]{stats['documents_loaded']}[/bold]\n"
        f"  Chunks generados:      [bold]{stats['chunks_created']}[/bold]\n"
        f"  Chunks almacenados:    [bold]{stats['chunks_stored']}[/bold]\n"
        f"  Tiempo total:          [bold]{stats['elapsed_seconds']}s[/bold]\n\n"
        f"Listo para el demo. Ejecuta:\n"
        f"[bold]streamlit run app/app.py[/bold]",
        title="Resultado",
    ))


if __name__ == "__main__":
    main()
