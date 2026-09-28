"""
Tests del pipeline del Lab 2, sin AWS.

Ejecutan las funciones reales de glue_job.py contra el dataset real del Lab 1.
Protegen el relato del workshop: si alguien recalibra el dataset o toca las
reglas, estos tests dicen que los numeros de los README dejaron de ser ciertos.

    pip install -r requirements.txt pytest
    pytest tests/ -v
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "labs/01-ingestion-data-lake/dataset/transactions.csv"
GLUE_JOB = REPO / "labs/02-transformation-quality-catalog/glue_job.py"


def _load_glue_job():
    spec = importlib.util.spec_from_file_location("glue_job", GLUE_JOB)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["glue_job"] = mod
    spec.loader.exec_module(mod)
    return mod


gj = _load_glue_job()


@pytest.fixture(scope="module")
def raw() -> pd.DataFrame:
    return pd.read_csv(DATASET, dtype=str)


@pytest.fixture(scope="module")
def stages(raw):
    """Las tres etapas del pipeline, medidas igual que en el job."""
    raw_m = gj.measure_rules(raw)
    transformed, tstats = gj.transform_dataframe(raw)
    tr_m = gj.measure_rules(transformed)
    valid, rejected, cur_m = gj.apply_data_quality(transformed)
    return {
        "raw": raw_m, "transformed": tr_m, "curated": cur_m,
        "valid": valid, "rejected": rejected, "tstats": tstats,
        "transformed_df": transformed,
    }


# ── El dataset trae los problemas que el lab necesita ────────────────────────

def test_dataset_tiene_1000_filas(raw):
    assert len(raw) == 1000


def test_el_dataset_falla_reglas_en_origen(stages):
    """Si el origen estuviera limpio, el Lab 2 no tendria nada que ensenar."""
    fallan = [n for n, r in stages["raw"]["rules"].items() if not r["passed"]]
    assert fallan, "ninguna regla falla en raw: el dataset perdio sus problemas deliberados"
    assert set(fallan) == {
        # de forma
        "uniqueness_transaction_id",
        "validity_payment_method",
        "validity_status",
        # de regla de negocio: las propias de un adquirente
        "ledger_coherence",
        "captured_implies_approved",
    }


# ── El ruleset declarado y lo que el job mide no pueden divergir ─────────────

def test_el_job_mide_las_10_reglas_del_dqdl(stages):
    dqdl = (REPO / "labs/02-transformation-quality-catalog/quality_rules.dqdl").read_text()
    # Solo lineas de regla: las palabras clave tambien salen en los comentarios.
    palabras = ("Completeness", "IsComplete", "Uniqueness", "ColumnValues",
                "ColumnLength", "CustomSql")
    declaradas = sum(
        1
        for linea in dqdl.splitlines()
        if (limpia := linea.strip()) and not limpia.startswith("#")
        and limpia.startswith(palabras)
    )
    assert len(stages["curated"]["rules"]) == declaradas == 10


# ── El relato raw -> transformado -> curated ──────────────────────────────────

def test_la_calidad_mejora_en_cada_etapa(stages):
    assert stages["raw"]["rules_passed"] == 5
    assert stages["transformed"]["rules_passed"] == 7
    # 10/10 porque las reglas de negocio tambien rechazan: una fila con el libro
    # descuadrado no debe llegar a liquidacion.
    assert stages["curated"]["rules_passed"] == 10


def test_la_transformacion_resuelve_tipos_y_estados(stages):
    """Valores recuperables: se normalizan y la fila se conserva."""
    for regla in ("validity_payment_method", "validity_status"):
        assert not stages["raw"]["rules"][regla]["passed"]
        assert stages["transformed"]["rules"][regla]["passed"]


@pytest.mark.parametrize(
    "regla",
    [
        # No hay forma de inventar cual de dos filas con el mismo id es la buena.
        "uniqueness_transaction_id",
        # Un libro descuadrado liquidaria mal al comercio: es dinero real.
        "ledger_coherence",
        # Capturada sin aprobacion del emisor es un contracargo esperando.
        "captured_implies_approved",
    ],
)
def test_lo_no_recuperable_se_resuelve_rechazando(stages, regla):
    """Transformar no puede arreglar esto: exige una decision de negocio."""
    assert not stages["raw"]["rules"][regla]["passed"]
    assert not stages["transformed"]["rules"][regla]["passed"]
    assert stages["curated"]["rules"][regla]["passed"]


# ── Invariantes del pipeline ─────────────────────────────────────────────────

def test_ninguna_fila_se_pierde(stages):
    total = len(stages["valid"]) + len(stages["rejected"])
    assert total == len(stages["transformed_df"]) == 1000


def test_se_rechazan_137_filas(stages):
    """Las cifras de los README dependen de este numero."""
    assert len(stages["rejected"]) == 137
    assert len(stages["valid"]) == 863


def test_los_rechazados_llevan_motivo(stages):
    rejected = stages["rejected"]
    assert "rejection_reason" in rejected.columns
    assert (rejected["rejection_reason"].str.len() > 0).all()


@pytest.mark.parametrize(
    "columna,permitidos",
    [
        ("payment_method", gj.VALID_PAYMENT_METHODS),
        ("status", gj.VALID_STATUSES),
        ("currency", gj.VALID_CURRENCIES),
    ],
)
def test_curated_no_contiene_valores_invalidos(stages, columna, permitidos):
    assert set(stages["valid"][columna].dropna().unique()) <= permitidos


def test_curated_sin_nulos_ni_duplicados(stages):
    valid = stages["valid"]
    assert not pd.to_numeric(valid["amount"], errors="coerce").isna().any()
    assert not valid["transaction_id"].duplicated().any()
    assert not valid["customer_id"].isna().any()


def test_curated_tiene_columnas_de_particion(stages):
    """write_curated_data particiona por year/month."""
    assert {"year", "month"} <= set(stages["valid"].columns)


# ── Casos borde ──────────────────────────────────────────────────────────────

def test_measure_rules_con_dataframe_vacio(raw):
    """Un raw/ vacio no debe reventar el job con ZeroDivisionError."""
    vacio = raw.iloc[0:0]
    m = gj.measure_rules(vacio)
    assert m["rules"] == {}
    assert m["overall_score"] == 1.0


def test_normalize_transaction_date_acepta_los_formatos_del_dataset():
    assert gj.normalize_transaction_date("2024-10-06") == "2024-10-06"
    assert gj.normalize_transaction_date("06/10/2024") is not None
    assert gj.normalize_transaction_date(None) is None
    assert gj.normalize_transaction_date("no es una fecha") is None


# ── Generador del dataset: la calibracion tiene que seguir siendo la documentada ──

def test_el_generador_reproduce_la_calibracion():
    """
    El generador no reproduce los CSV byte a byte, pero si sus porcentajes.
    Si alguien cambia una fraccion, este test lo dice.
    """
    gen_path = REPO / "labs/01-ingestion-data-lake/dataset/generate_dataset.py"
    spec = importlib.util.spec_from_file_location("generate_dataset", gen_path)
    gen = importlib.util.module_from_spec(spec)
    sys.modules["generate_dataset"] = gen
    spec.loader.exec_module(gen)

    assert gen.FRACTION_NULL_AMOUNT == 0.050
    assert gen.FRACTION_LEGACY_METHOD == 0.020
    assert gen.FRACTION_WEIRD_STATUS == 0.080
    assert gen.FRACTION_NON_ISO_DATE == 0.100
    # Las dos propias de la adquirencia
    assert gen.FRACTION_LEDGER_MISMATCH == 0.030
    assert gen.FRACTION_APPROVED_NO_AUTH == 0.015

    # La fraccion de nulos es la que pone completeness justo en el umbral.
    completeness = 1 - gen.FRACTION_NULL_AMOUNT
    assert completeness == pytest.approx(0.95), (
        "cambiar FRACTION_NULL_AMOUNT mueve la regla R01 fuera del borde del umbral, "
        "que es donde el Lab 2 la quiere"
    )


def test_el_csv_del_repo_coincide_con_la_calibracion(raw):
    """Los CSV commiteados son la referencia: deben tener las cantidades documentadas."""
    assert pd.to_numeric(raw["amount"], errors="coerce").isna().sum() == 50
    assert (~raw["payment_method"].isin(gj.VALID_PAYMENT_METHODS)).sum() == 20
    assert (~raw["status"].isin(gj.VALID_STATUSES)).sum() == 80
    assert raw["transaction_date"].str.contains("/").sum() == 100
    assert raw["transaction_id"].duplicated(keep=False).sum() == 53


def test_el_csv_tiene_las_columnas_de_la_vista_del_adquirente(raw):
    """
    El contrato del dataset es la vista acquiring.v_transactions_for_ingestion.

    Si divergen, el dataset historico y el flujo en tiempo real dejan de tener la
    misma forma, y el Lab 2 tendria que distinguirlos.
    """
    import re as _re

    sql = (REPO / "payments/schema/001_schema.sql").read_text(encoding="utf-8")
    bloque = sql.split("CREATE OR REPLACE VIEW v_transactions_for_ingestion")[1]
    bloque = bloque.split("FROM charges")[0]

    vista = []
    for linea in bloque.splitlines():
        linea = _re.sub(r"--.*$", "", linea).strip().rstrip(",")
        if not linea or linea.startswith("SELECT"):
            continue
        alias = _re.search(r"AS\s+([a-z_]+)$", linea)
        if alias:
            vista.append(alias.group(1))
        elif _re.fullmatch(r"[a-z]+\.([a-z_]+)", linea):
            vista.append(linea.split(".")[1])

    assert list(raw.columns) == vista, (
        "las columnas del CSV ya no coinciden con la vista del adquirente"
    )


# ── Lab 2 -> Lab 3: los documentos derivados ─────────────────────────────────

def test_los_documentos_derivados_son_citables(stages):
    """
    Un vector store con filas de CSV no responde preguntas de negocio.
    ingest_from_curated.py convierte los datos curados en hechos agregados.
    """
    spec = importlib.util.spec_from_file_location(
        "ingest_from_curated", REPO / "scripts/ingest_from_curated.py"
    )
    ifc = importlib.util.module_from_spec(spec)
    sys.modules["ingest_from_curated"] = ifc
    spec.loader.exec_module(ifc)

    valid = stages["valid"]
    documentos = {
        "mensual": ifc.doc_monthly_summary(valid),
        "riesgo": ifc.doc_risk_profile(valid),
    }
    for nombre, texto in documentos.items():
        assert len(texto.splitlines()) > 10, f"{nombre} sale demasiado corto"
        # Un 'nan' o un 'None' en el corpus se convierte en una cita absurda.
        assert "nan" not in texto.lower().replace("financ", "")
        assert "None" not in texto


# ── El runner local: que el job entero siga corriendo sin AWS ────────────────

def test_el_pipeline_completo_corre_en_local(tmp_path):
    """
    Ejecuta glue_job.main() de punta a punta con el doble de S3.

    Cubre lo que los tests de funciones puras no ven: parse_args, el orden de
    los pasos en main(), la escritura de Parquet particionado, y que el reporte
    se serialice a JSON sin reventar.
    """
    import shutil

    spec = importlib.util.spec_from_file_location(
        "run_pipeline_local", REPO / "scripts/run_pipeline_local.py"
    )
    runner = importlib.util.module_from_spec(spec)
    sys.modules["run_pipeline_local"] = runner
    spec.loader.exec_module(runner)

    root = tmp_path / "lake"
    root.mkdir()
    for tabla in ("transactions", "merchants"):
        destino = root / "raw" / tabla / "LOAD00000001.csv"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO / f"labs/01-ingestion-data-lake/dataset/{tabla}.csv", destino)

    fake_s3 = runner.LocalS3(root)
    job = _load_glue_job()
    job.boto3.client = lambda *a, **k: fake_s3

    argv = sys.argv
    sys.argv = ["glue_job.py", "--S3_BUCKET", "local", "--JOB_NAME", "test-etl"]
    try:
        job.main()
    finally:
        sys.argv = argv

    # Parquet particionado por year/month, como espera el Crawler
    parquets = list((root / "curated").rglob("*.parquet"))
    assert parquets, "el job no escribio ningun Parquet"
    assert any("year=" in str(p) and "month=" in str(p) for p in parquets)

    # Los rechazados se apartan con su motivo, no se borran
    rejected = list((root / "rejected").rglob("*.csv"))
    assert rejected, "el job no escribio los rechazados"
    assert "rejection_reason" in rejected[0].read_text(encoding="utf-8")

    # El reporte publica la comparacion por etapas
    report = json.loads((root / "quality-reports/latest/quality_report.json").read_text())
    assert report["summary"]["total_rows_rejected"] == 137
    comparacion = report["rules_raw_vs_curated"]
    assert comparacion["validity_status"]["resolved_by"] == "transformacion"
    assert comparacion["uniqueness_transaction_id"]["resolved_by"] == "rechazo"
    assert comparacion["ledger_coherence"]["resolved_by"] == "rechazo"


# ── El puente entre los datos (Labs 1-2) y el corpus (Lab 3) ─────────────────
# El corpus estaba en prosa espanola y el dataset en codigos enum en ingles:
# compartian el dominio pero no los tokens, asi que una pregunta que mezclara
# ambos mundos no recuperaba nada. monitoreo_operativo.md es el puente.

CORPUS = REPO / "labs/03-data-to-ai/data"


@pytest.fixture(scope="module")
def corpus_texto() -> str:
    return "\n".join(
        p.read_text(encoding="utf-8") for p in sorted(CORPUS.rglob("*.md"))
    )


@pytest.mark.parametrize(
    "valor",
    [
        # payment_method
        "CARD_PRESENT", "QR",
        # status
        "CAPTURED", "FAILED", "REFUNDED",
        # currency
        "USD", "PEN", "EUR",
        # risk_level de customers.csv
        "LOW", "MEDIUM", "HIGH",
    ],
)
def test_el_corpus_usa_el_vocabulario_del_dataset(corpus_texto, valor):
    """
    Cada valor que el RAG puede recibir en una pregunta tiene que existir en el
    corpus. Si no, el retrieval no puede conectar la pregunta con la politica.
    """
    assert re.search(rf"\b{re.escape(valor)}\b", corpus_texto), (
        f"'{valor}' no aparece en el corpus: una pregunta que lo use no recuperara nada"
    )


@pytest.mark.parametrize(
    "campo",
    ["transaction_id", "customer_id", "terminal_id", "device_serial",
     "rejection_reason", "transactions_curated", "curated/", "rejected/",
     # Propios del adquirente: si el corpus no los menciona, el analista no puede
     # interpretar lo que ve en la tabla.
     "mcc", "response_code", "rrn", "fee_amount", "net_amount"],
)
def test_el_corpus_referencia_la_plataforma_de_datos(corpus_texto, campo):
    """El corpus debe hablar de las zonas y campos que producen los Labs 1 y 2."""
    assert campo in corpus_texto, f"el corpus no menciona '{campo}'"


def test_los_valores_del_dataset_estan_todos_cubiertos(raw):
    """
    Lo mismo, pero partiendo del dataset: cualquier valor que exista en los datos
    reales debe estar documentado en el corpus. Asi, si alguien anade un tipo de
    transaccion nuevo al generador, el test avisa de que falta documentarlo.
    """
    corpus = "\n".join(p.read_text(encoding="utf-8") for p in sorted(CORPUS.rglob("*.md")))
    for columna in ("payment_method", "status", "currency"):
        valores = {v for v in raw[columna].dropna().unique() if v.isascii() and v.isupper()}
        faltan = [v for v in valores if not re.search(rf"\b{re.escape(v)}\b", corpus)]
        assert not faltan, f"valores de {columna} sin documentar en el corpus: {faltan}"


def test_la_matriz_de_decision_sobrevive_al_chunking():
    """
    La matriz de decision es la unidad que el RAG tiene que recuperar entera: sin
    su cabecera, una fila como '| WITHDRAWAL | HIGH | ... |' no significa nada.
    Con chunk_size=1000 cabe justa, asi que conviene vigilarlo.
    """
    pytest.importorskip("langchain_text_splitters")
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    doc = (CORPUS / "compliance/monitoreo_operativo.md").read_text(encoding="utf-8")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1000, chunk_overlap=200, separators=["\n\n", "\n", ". ", " ", ""]
    )
    chunks = splitter.split_text(doc)

    con_fila = [c for c in chunks if "`WITHDRAWAL` | `HIGH`" in c]
    assert con_fila, "la fila critica de la matriz no aparece en ningun chunk"
    assert any(
        "transaction_type` | `risk_level" in c or "## 5. Matriz" in c for c in con_fila
    ), "la fila se separo de la cabecera de su tabla: el chunk pierde el significado"
