"""
Lab 2 -> Lab 3: los hechos derivados, generados de lo que hay de verdad en S3.

test_glue_job.py prueba dos de los documentos sobre el DataFrame en memoria. Aqui
se generan TODOS leyendo la zona curada como la lee el script —Parquet con
year/month en la ruta— y la telemetria como la deja Firehose. Fue leyendo de
S3 donde aparecio que el resumen mensual fallaba: year y month no estan dentro
del Parquet.
"""

from __future__ import annotations

import gzip
import importlib.util
import json
import sys
from datetime import datetime, timezone

import pandas as pd
import pytest

from tests.test_api import REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


runner = _cargar("runner_docs", REPO_ROOT / "scripts/run_pipeline_local.py")
ifc = _cargar("ifc_docs", REPO_ROOT / "scripts/ingest_from_curated.py")

# El core sintetico del emulador fecha sus cobros hasta el 2026-10-02 15:00 UTC.
AHORA = datetime(2026, 10, 2, 16, 0, tzinfo=timezone.utc)


@pytest.fixture(scope="module")
def lago(tmp_path_factory):
    """El pipeline del Lab 2 de punta a punta, mas la telemetria de la flota."""
    import generate_traffic as gt

    root = tmp_path_factory.mktemp("lago")
    runner.seed_raw_zone(root)
    s3 = runner.LocalS3(root)

    job = _cargar("glue_docs", REPO_ROOT / "labs/02-transformation-quality-catalog/glue_job.py")
    job.boto3.client = lambda *a, **k: s3
    argv, sys.argv = sys.argv, ["glue_job.py", "--S3_BUCKET", "local"]
    try:
        job.main()
    finally:
        sys.argv = argv

    # Telemetria como la deja Firehose: JSON por linea, GZIP. La flota virtual
    # sin trafico: solo late.
    # Un latido cada 30 s durante los ultimos 15 minutos: el terminal que se
    # queda sin bateria deja de latir a mitad, y su ultimo latido envejece.
    latidos = []
    rondas = 30
    for ronda in range(rondas):
        ms = int(AHORA.timestamp() * 1000) - (rondas - 1 - ronda) * 30_000
        gen_publish = (lambda ms: lambda topic, m: latidos.append(
            {**m, "device_id": topic.split("/")[0], "received_at_ms": ms}))(ms)
        if ronda == 0:
            gen = gt.Generador(lambda *a: (0, {}), {}, __import__("random").Random(5),
                               publish=gen_publish)
        gen.publish = gen_publish
        gen.latido()
    destino = root / "raw/telemetry/year=2026/month=10/day=02/lote.gz"
    destino.parent.mkdir(parents=True)
    destino.write_bytes(gzip.compress("\n".join(json.dumps(x) for x in latidos).encode()))
    return root, s3


@pytest.fixture(scope="module")
def documentos(lago):
    root, s3 = lago
    df = ifc.read_curated(s3, "local", "curated/transactions/")
    report = ifc.read_quality_report(s3, "local")
    tele = ifc.read_telemetry(s3, "local")
    return {
        "mensual": ifc.doc_monthly_summary(df),
        "calidad": ifc.doc_quality_summary(report, df),
        "riesgo": ifc.doc_risk_profile(df),
        "canales": ifc.doc_channels_and_issuer(df),
        "terminales": ifc.doc_terminal_health(tele, df, now=AHORA),
        # "Ahora" es el momento del ultimo pago: el core sintetico reparte sus
        # cobros en 60 dias, y lo que importa es que el documento cuente los del
        # dia de ese pago en hora de Lima.
        "hoy": ifc.doc_today(df, now=pd.to_datetime(
            df["created_at"], utc=True, errors="coerce").max().to_pydatetime()),
    }


def test_year_y_month_salen_de_la_ruta(lago):
    _, s3 = lago
    df = ifc.read_curated(s3, "local", "curated/transactions/")
    assert {"year", "month"} <= set(df.columns)
    assert df["year"].notna().all()


@pytest.mark.parametrize("nombre", ["mensual", "calidad", "riesgo", "canales", "terminales", "hoy"])
def test_ningun_documento_cita_valores_vacios(documentos, nombre):
    texto = documentos[nombre]
    assert len(texto.splitlines()) > 5
    # Un 'nan' o un 'None' en el corpus se convierte en una cita absurda.
    assert "nan" not in texto.lower().replace("financ", "")
    assert "None" not in texto


def test_la_calidad_dice_de_donde_vienen_los_rechazos(documentos):
    t = documentos["calidad"]
    assert "**acquiring**" in t and "Ninguna regla fallaba en origen" in t
    assert "**legacy**" in t and "DUPLICATE_TXN_ID" in t


def test_los_canales_explican_los_codigos_de_rechazo(documentos):
    t = documentos["canales"]
    assert "Canal CARD_NOT_PRESENT" in t
    assert "51 (fondos insuficientes)" in t


def test_la_salud_de_terminales_encuentra_los_dos_problemas(documentos):
    t = documentos["terminales"]
    # El taxi con mala cobertura: la red, no el emisor.
    bloque_6 = t[t.index("## Terminal TID00006"):].split("## ")[1]
    assert "Senal critica" in bloque_6
    # El taxi sin bateria: dejo de latir.
    bloque_7 = t[t.index("## Terminal TID00007"):].split("## ")[1]
    assert "offline" in bloque_7 and "sin carga" in bloque_7


def test_la_actividad_de_hoy_cuenta_los_pagos_del_dia_en_hora_de_lima(documentos):
    t = documentos["hoy"]
    assert "Hoy se proces" in t
    assert "Por canal:" in t


def test_hoy_es_el_dia_de_lima_y_no_el_utc():
    """A las 21:00 de Lima ya es manana en UTC: el pago sigue siendo de hoy."""
    df = pd.DataFrame([{
        "created_at": "2026-10-03T02:00:00+00:00",   # 21:00 del 2 en Lima
        "amount": "10.00", "currency": "PEN", "response_code": "00",
        "payment_method": "QR", "merchant_name": "Picanteria Arequipa",
    }])
    t = ifc.doc_today(df, now=datetime(2026, 10, 3, 2, 30, tzinfo=timezone.utc))
    assert "2026-10-02" in t and "Hoy se proceso 1 pago," in t
