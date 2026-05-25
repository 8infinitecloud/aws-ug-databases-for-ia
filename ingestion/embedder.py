"""
Generación de embeddings con Amazon Titan Embeddings V2 via Bedrock.

Por qué Titan V2 (ver ADR-003): está en Bedrock (mismo IAM role, sin API key externa),
soporta español, y las 1024 dimensiones ofrecen buena calidad de retrieval para
el corpus de este lab.

La función incluye retry con backoff exponencial porque Bedrock tiene throttling
por defecto para cuentas nuevas (10 RPM para Titan Embeddings).
"""

from __future__ import annotations

import time
from typing import Callable

import boto3
from langchain_aws import BedrockEmbeddings
from langchain_core.documents import Document
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from tenacity import retry, stop_after_attempt, wait_exponential

from ingestion.config import bedrock as bedrock_cfg

console = Console()


def get_embeddings_model() -> BedrockEmbeddings:
    """
    Retorna el modelo de embeddings configurado.

    Separar la construcción del modelo de su uso permite mockear en tests
    sin levantar un cliente Bedrock real.
    """
    bedrock_client = boto3.client(
        service_name="bedrock-runtime",
        region_name=bedrock_cfg.region,
    )

    return BedrockEmbeddings(
        client=bedrock_client,
        model_id=bedrock_cfg.embedding_model,
        # normalize=True garantiza que las distancias coseno sean comparables entre queries.
        # Titan V2 lo hace por defecto, pero ser explícito evita sorpresas si se cambia el modelo.
        normalize=True,
    )


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    reraise=True,
)
def embed_documents_batch(
    chunks: list[Document],
    embeddings_model: BedrockEmbeddings,
    batch_size: int = 10,
) -> list[tuple[Document, list[float]]]:
    """
    Genera embeddings para una lista de chunks en batches para no saturar la API.

    Retorna tuplas (chunk, embedding) para mantener la correspondencia
    entre el texto original y su vector.

    batch_size=10: Titan V2 acepta hasta 2048 tokens por llamada. Batches de 10
    chunks de 1000 chars (~250 tokens cada uno) quedan bien por debajo del límite.
    """
    results: list[tuple[Document, list[float]]] = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        TaskProgressColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("Generando embeddings...", total=len(chunks))

        for i in range(0, len(chunks), batch_size):
            batch = chunks[i : i + batch_size]
            texts = [doc.page_content for doc in batch]

            embeddings = embeddings_model.embed_documents(texts)

            for doc, embedding in zip(batch, embeddings):
                results.append((doc, embedding))

            progress.advance(task, len(batch))

            # Pausa entre batches para respetar el throttling de Bedrock.
            # En producción con Provisioned Throughput, eliminar este sleep.
            if i + batch_size < len(chunks):
                time.sleep(0.5)

    return results


def embed_query(query: str, embeddings_model: BedrockEmbeddings) -> list[float]:
    """
    Genera el embedding de una query de usuario para la búsqueda por similitud.

    Mismo modelo que en ingesta: es crítico que query y documentos tengan
    el mismo espacio vectorial. Mezclar modelos produce resultados sin sentido.
    """
    return embeddings_model.embed_query(query)
