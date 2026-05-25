"""
Carga de documentos desde sistema de archivos local o S3.

Por qué cargar desde archivos locales en el lab:
Los documentos de ejemplo están en data/. En producción se cargarían desde S3
usando el mismo S3DirectoryLoader de LangChain — la diferencia es solo la fuente,
el resto del pipeline es idéntico.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Generator

import boto3
from langchain_community.document_loaders import (
    DirectoryLoader,
    TextLoader,
    UnstructuredMarkdownLoader,
)
from langchain_core.documents import Document
from rich.console import Console

console = Console()


def load_from_directory(data_dir: str | Path) -> list[Document]:
    """
    Carga todos los archivos Markdown de un directorio, preservando metadatos
    de ruta para el filtrado post-retrieval en la UI.
    """
    data_path = Path(data_dir)
    if not data_path.exists():
        raise FileNotFoundError(f"Directorio no encontrado: {data_path}")

    # UnstructuredMarkdownLoader preserva estructura de headers como metadata,
    # útil para mostrar en qué sección del documento está cada chunk.
    loader = DirectoryLoader(
        str(data_path),
        glob="**/*.md",
        loader_cls=UnstructuredMarkdownLoader,
        show_progress=True,
        use_multithreading=True,
    )

    documents = loader.load()

    # Enriquecer metadata con categoría derivada de la ruta del archivo.
    # Esto permite filtrar por tipo de documento en el retrieval.
    for doc in documents:
        source_path = Path(doc.metadata.get("source", ""))
        doc.metadata["doc_type"] = _infer_doc_type(source_path)
        doc.metadata["filename"] = source_path.name
        doc.metadata["relative_path"] = str(
            source_path.relative_to(data_path) if data_path in source_path.parents else source_path
        )

    console.print(f"[green]✓[/green] Cargados {len(documents)} documentos desde {data_path}")
    return documents


def load_from_s3(bucket: str, prefix: str) -> list[Document]:
    """
    Descarga documentos desde S3 a un directorio temporal y los carga.
    En producción, usar S3DirectoryLoader de langchain-community directamente.
    """
    import tempfile

    s3_client = boto3.client("s3")
    paginator = s3_client.get_paginator("list_objects_v2")

    documents = []
    with tempfile.TemporaryDirectory() as tmp_dir:
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".md"):
                    continue

                local_path = Path(tmp_dir) / Path(key).name
                s3_client.download_file(bucket, key, str(local_path))
                console.print(f"  ↓ Descargado: {key}")

        documents = load_from_directory(tmp_dir)

    return documents


def _infer_doc_type(path: Path) -> str:
    """Deriva el tipo de documento desde la ruta para usarlo como metadata de filtrado."""
    parts = path.parts
    if "compliance" in parts:
        return "compliance"
    elif "onboarding" in parts:
        return "onboarding"
    elif "policies" in parts:
        return "policy"
    return "general"
