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
        "uniqueness_transaction_id",
        "validity_transaction_type",
        "validity_status",
    }


# ── El ruleset declarado y lo que el job mide no pueden divergir ─────────────

def test_el_job_mide_las_8_reglas_del_dqdl(stages):
    dqdl = (REPO / "labs/02-transformation-quality-catalog/quality_rules.dqdl").read_text()
    # Solo lineas de regla: las palabras clave tambien salen en los comentarios.
    palabras = ("Completeness", "IsComplete", "Uniqueness", "ColumnValues", "ColumnLength")
    declaradas = sum(
        1
        for linea in dqdl.splitlines()
        if (limpia := linea.strip()) and not limpia.startswith("#")
        and limpia.startswith(palabras)
    )
    assert len(stages["curated"]["rules"]) == declaradas == 8


# ── El relato raw -> transformado -> curated ──────────────────────────────────

def test_la_calidad_mejora_en_cada_etapa(stages):
    assert stages["raw"]["rules_passed"] == 5
    assert stages["transformed"]["rules_passed"] == 7
    assert stages["curated"]["rules_passed"] == 8


def test_la_transformacion_resuelve_tipos_y_estados(stages):
    """Valores recuperables: se normalizan y la fila se conserva."""
    for regla in ("validity_transaction_type", "validity_status"):
        assert not stages["raw"]["rules"][regla]["passed"]
        assert stages["transformed"]["rules"][regla]["passed"]


def test_los_duplicados_se_resuelven_rechazando_no_transformando(stages):
    """No hay forma de inventar cual de dos filas con el mismo id es la buena."""
    regla = "uniqueness_transaction_id"
    assert not stages["raw"]["rules"][regla]["passed"]
    assert not stages["transformed"]["rules"][regla]["passed"]
    assert stages["curated"]["rules"][regla]["passed"]


# ── Invariantes del pipeline ─────────────────────────────────────────────────

def test_ninguna_fila_se_pierde(stages):
    total = len(stages["valid"]) + len(stages["rejected"])
    assert total == len(stages["transformed_df"]) == 1000


def test_se_rechazan_100_filas(stages):
    """Las cifras de los README dependen de este numero."""
    assert len(stages["rejected"]) == 100
    assert len(stages["valid"]) == 900


def test_los_rechazados_llevan_motivo(stages):
    rejected = stages["rejected"]
    assert "rejection_reason" in rejected.columns
    assert (rejected["rejection_reason"].str.len() > 0).all()


@pytest.mark.parametrize(
    "columna,permitidos",
    [
        ("transaction_type", gj.VALID_TRANSACTION_TYPES),
        ("status", gj.VALID_STATUSES),
        ("currency", gj.VALID_CURRENCIES),
    ],
)
def test_curated_no_contiene_valores_invalidos(stages, columna, permitidos):
    assert set(stages["valid"][columna].dropna().unique()) <= permitidos


def test_curated_sin_nulos_ni_duplicados(stages):
    valid = stages["valid"]
    assert not valid["amount"].isna().any()
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
    assert gen.FRACTION_SPANISH_TYPE == 0.020
    assert gen.FRACTION_WEIRD_STATUS == 0.080
    assert gen.FRACTION_NON_ISO_DATE == 0.100

    # La fraccion de nulos es la que pone completeness justo en el umbral.
    completeness = 1 - gen.FRACTION_NULL_AMOUNT
    assert completeness == pytest.approx(0.95), (
        "cambiar FRACTION_NULL_AMOUNT mueve la regla R01 fuera del borde del umbral, "
        "que es donde el Lab 2 la quiere"
    )


def test_el_csv_del_repo_coincide_con_la_calibracion(raw):
    """Los CSV commiteados son la referencia: deben tener las cantidades documentadas."""
    assert raw["amount"].isna().sum() == 50
    assert (~raw["transaction_type"].isin(gj.VALID_TRANSACTION_TYPES)).sum() == 20
    assert (~raw["status"].isin(gj.VALID_STATUSES)).sum() == 80
    assert raw["transaction_date"].str.contains("/").sum() == 100
    assert raw["transaction_id"].duplicated(keep=False).sum() == 55


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
    for tabla in ("transactions", "customers"):
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
    assert report["summary"]["total_rows_rejected"] == 100
    comparacion = report["rules_raw_vs_curated"]
    assert comparacion["validity_status"]["resolved_by"] == "transformacion"
    assert comparacion["uniqueness_transaction_id"]["resolved_by"] == "rechazo"
