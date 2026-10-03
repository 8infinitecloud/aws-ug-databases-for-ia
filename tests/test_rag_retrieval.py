"""
Lab 3: la recuperacion del RAG, sin Aurora ni Bedrock.

Tres fallos que aparecieron preguntandole al asistente en AWS, con el dato en
el corpus:

  - "¿que comercios hicieron pagos hoy?" dejaba el documento correcto el primero
    con 0.687, y el umbral de 0.70 lo descartaba: contexto vacio, "no se".
  - "¿y a que hora fueron?" no encontraba nada: la busqueda solo veia la
    pregunta suelta, sin la anterior que decia de que se hablaba.
  - min_score=0.0 se convertia en 0.70 (un `or` donde hacia falta `is None`).

Las dependencias de AWS del lab (langchain_aws, redis...) no estan en el CI: si
faltan, se sustituyen por modulos vacios. Solo las que faltan, para no pisar las
de otros tests.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from unittest.mock import MagicMock

import pytest

from tests.test_api import REPO_ROOT

LAB3 = REPO_ROOT / "labs/03-data-to-ai"

for _mod in ("langchain_aws", "langchain_community", "langchain_community.document_loaders",
             "redis", "psycopg2", "psycopg2.extras"):
    try:
        importlib.import_module(_mod)
    except ImportError:
        stub = types.ModuleType(_mod)
        stub.__getattr__ = lambda name: MagicMock(name=name)  # type: ignore[attr-defined]
        sys.modules[_mod] = stub

# Al final del path, no al principio: el backend de pagos tambien tiene un
# paquete llamado `app`, y el del lab no debe taparlo.
if str(LAB3) not in sys.path:
    sys.path.append(str(LAB3))

from ingestion.config import retrieval as retrieval_cfg  # noqa: E402
from ingestion.loader import _infer_doc_type  # noqa: E402
from query import chain, retriever  # noqa: E402
from query.memory import SessionMessage  # noqa: E402
from query.retriever import RetrievedChunk, merge_results  # noqa: E402


def _chunk(source, idx, score):
    return RetrievedChunk(content=f"{source}#{idx}", score=score, source=source,
                          doc_type="operations", chunk_index=idx, total_chunks=3,
                          content_preview="")


def test_el_umbral_deja_pasar_la_pregunta_corta_que_fallaba():
    # El score medido en AWS para "¿que comercios hicieron pagos hoy?".
    assert retrieval_cfg.min_score <= 0.687


def test_merge_quita_duplicados_y_se_queda_con_el_mejor_score():
    a = [_chunk("hoy.md", 0, 0.60), _chunk("kyc.md", 2, 0.65)]
    b = [_chunk("hoy.md", 0, 0.78), _chunk("aml.md", 1, 0.61)]
    juntos = merge_results(a, b, top_k=2)
    assert [(c.source, c.score) for c in juntos] == [("hoy.md", 0.78), ("kyc.md", 0.65)]


def test_un_umbral_de_cero_es_cero(monkeypatch):
    """Con `or`, min_score=0.0 caia en el de la config y escondia los scores."""
    visto = {}

    class Cursor:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, sql, params):
            visto.update(params)

        def fetchall(self):
            return []

    conn = MagicMock()
    conn.__enter__.return_value.cursor.return_value = Cursor()
    monkeypatch.setattr(retriever, "get_connection", lambda: conn)
    retriever.similarity_search("x", top_k=3, min_score=0.0, query_embedding=[0.1])
    assert visto["min_score"] == 0.0 and visto["top_k"] == 3


@pytest.fixture
def cadena(monkeypatch):
    """La cadena con todo lo externo sustituido; registra cada busqueda."""
    busquedas = []
    historial = []

    def buscar(query, **kw):
        busquedas.append(query)
        # La pregunta suelta no encuentra el documento; con contexto, si.
        return [_chunk("operaciones-actividad-hoy.md", 1, 0.75)] if "hoy" in query else []

    monkeypatch.setattr(chain, "get_embeddings_model", lambda: None)
    monkeypatch.setattr(chain, "embed_query", lambda q, m: [0.0])
    monkeypatch.setattr(chain, "similarity_search", buscar)
    monkeypatch.setattr(chain, "get_session_messages", lambda sid: list(historial))
    monkeypatch.setattr(chain, "build_memory_context", lambda **kw: MagicMock())
    monkeypatch.setattr(chain, "build_prompt", lambda **kw: ("sys", "human"))
    monkeypatch.setattr(chain, "add_session_message", lambda *a: None)
    monkeypatch.setattr(chain, "format_sources", lambda chunks: [c.source for c in chunks])
    llm = MagicMock()
    llm.invoke.return_value.content = "respuesta"
    monkeypatch.setattr(chain, "get_llm", lambda: llm)
    return busquedas, historial


def test_la_primera_pregunta_se_busca_una_vez(cadena):
    busquedas, _ = cadena
    chain.answer_question("¿Cuantos pagos hubo hoy?", "u", "s")
    assert busquedas == ["¿Cuantos pagos hubo hoy?"]


def test_un_seguimiento_se_busca_tambien_con_la_pregunta_anterior(cadena):
    busquedas, historial = cadena
    historial += [SessionMessage("user", "¿Que comercios pagaron hoy?", "t1"),
                  SessionMessage("assistant", "Mercado Andino", "t2")]
    r = chain.answer_question("¿y a que hora fueron?", "u", "s")
    assert busquedas == ["¿y a que hora fueron?",
                         "¿Que comercios pagaron hoy?\n¿y a que hora fueron?"]
    assert r.sources == ["operaciones-actividad-hoy.md"]


def test_los_hechos_derivados_son_de_operacion():
    assert _infer_doc_type(LAB3 / "data/derived/operaciones-actividad-hoy.md") == "operations"
    assert _infer_doc_type(LAB3 / "data/compliance/aml_policy.md") == "compliance"
