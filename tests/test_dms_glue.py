"""
Lab 1 -> Lab 2: lo que deja DMS y lo que hace Glue con ello.

La primera vez que se ejecutaron juntos en AWS fallaron por cosas que ninguno de
los dos veia por separado: rutas, cabeceras, columnas perdidas. Estas pruebas
juntan las dos mitades sin AWS, con el layout exacto de DMS
(scripts/dms_emulator.py).

La prueba que importa es la ultima: trafico real por la API, dos fotos del core
con su CDC entre medias, y el resultado de Glue comparado fila a fila con la
vista v_transactions_for_ingestion de la base. Si el JOIN de Glue y la vista
divergen, falla aqui y no en Athena delante del publico.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tests.test_api import REPO_ROOT, _db_reachable

sys.path.insert(0, str(REPO_ROOT / "scripts"))

CONNINFO = "host=localhost port=55433 dbname=acquiring user=postgres password=demo"


def _cargar(nombre, ruta):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[nombre] = mod
    spec.loader.exec_module(mod)
    return mod


gj = _cargar("glue_job_dms", REPO_ROOT / "labs/02-transformation-quality-catalog/glue_job.py")
runner = _cargar("run_pipeline_local_dms", REPO_ROOT / "scripts/run_pipeline_local.py")


def _leer(root):
    return gj.read_raw_data(runner.LocalS3(root), "local", "raw/")


# ── El CDC: de muchas versiones, el estado actual ─────────────────────────────
def _versiones(*filas):
    return pd.DataFrame([
        {"Op": op, "dms_timestamp": pd.Timestamp(ts), "id": i, "status": st}
        for op, ts, i, st in filas
    ])


def test_gana_la_ultima_version_de_cada_fila():
    """El cobro que llega como I (pendiente) y despues como U (cobrado)."""
    v = _versiones(("I", "2026-10-02 10:00:00", 1, "REQUIRES_CONFIRMATION"),
                   ("U", "2026-10-02 10:00:30", 1, "SUCCEEDED"),
                   ("I", "2026-10-02 10:00:10", 2, "SUCCEEDED"))
    vivas, stats = gj.apply_cdc(v)
    assert dict(zip(vivas["id"], vivas["status"])) == {1: "SUCCEEDED", 2: "SUCCEEDED"}
    assert stats["updates_applied"] == 1
    assert "Op" not in vivas.columns and "dms_timestamp" not in vivas.columns


def test_un_delete_hace_desaparecer_la_fila():
    v = _versiones(("I", "2026-10-02 10:00:00", 1, "X"), ("D", "2026-10-02 10:01:00", 1, "X"))
    vivas, stats = gj.apply_cdc(v)
    assert vivas.empty and stats["deletes"] == 1


def test_el_orden_de_llegada_no_importa_manda_el_timestamp():
    """Los ficheros se leen por nombre; un U puede leerse antes que su I."""
    v = _versiones(("U", "2026-10-02 10:00:30", 1, "SUCCEEDED"),
                   ("I", "2026-10-02 10:00:00", 1, "REQUIRES_CONFIRMATION"))
    vivas, _ = gj.apply_cdc(v)
    assert vivas.iloc[0]["status"] == "SUCCEEDED"


def test_en_el_mismo_milisegundo_desempata_el_orden_de_lectura():
    v = _versiones(("I", "2026-10-02 10:00:00", 1, "A"), ("U", "2026-10-02 10:00:00", 1, "B"))
    vivas, _ = gj.apply_cdc(v)
    assert vivas.iloc[0]["status"] == "B"


# ── Con el core sintetico (sin base) ─────────────────────────────────────────
def test_glue_lee_el_layout_de_dms_y_aplica_los_cambios(tmp_path):
    import dms_emulator as dms

    antes, despues = dms.synthesize(cobros=80)
    dms.write_layout(tmp_path, antes,
                     [(datetime.now(timezone.utc) + timedelta(minutes=1), dms.cdc_delta(antes, despues))])

    raw, meta = _leer(tmp_path)
    core = raw[raw["source"] == "acquiring"]

    # Una fila por cobro del estado FINAL: los nuevos (I) estan, y ninguno sale dos veces.
    assert len(core) == len(despues["charges"])
    assert core["transaction_id"].is_unique
    assert meta["acquiring"]["tables"]["payment_intents"]["updates_applied"] > 0

    # El libro cuadra fila a fila: lo que la base garantiza sigue cierto en raw.
    cap = core[core["status"] == "CAPTURED"]
    diff = (cap["amount"].astype(float) - cap["fee_amount"].astype(float)
            - cap["net_amount"].astype(float)).abs()
    assert (diff < 0.005).all()


def test_las_dos_fuentes_llevan_su_marca(tmp_path):
    runner.seed_raw_zone(tmp_path)
    raw, meta = _leer(tmp_path)
    assert set(raw["source"]) == {"acquiring", "legacy"}
    assert meta["rows_by_source"]["legacy"] == 1000


def test_sin_dms_todavia_el_job_corre_solo_con_el_legacy(tmp_path):
    destino = tmp_path / "raw/legacy/transactions/transactions.csv"
    destino.parent.mkdir(parents=True)
    destino.write_text((REPO_ROOT / "labs/01-ingestion-data-lake/dataset/transactions.csv").read_text())
    raw, _ = _leer(tmp_path)
    assert set(raw["source"]) == {"legacy"}


def test_sin_ninguna_fuente_falla_explicando_que_mirar(tmp_path):
    with pytest.raises(FileNotFoundError, match="DMS"):
        _leer(tmp_path)


def test_el_core_no_trae_problemas_y_el_legacy_si(tmp_path):
    """El relato del Lab 2: la base impide los errores; la deuda viene de fuera."""
    runner.seed_raw_zone(tmp_path)
    raw, _ = _leer(tmp_path)
    por_fuente = {s: gj.measure_rules(g) for s, g in raw.groupby("source")}
    assert por_fuente["acquiring"]["rules_failed"] == 0
    assert por_fuente["legacy"]["rules_failed"] == 5

    transformado, _ = gj.transform_dataframe(raw)
    validas, rechazadas, _ = gj.apply_data_quality(transformado)
    resumen = gj.summarize_by_source(por_fuente, validas, rechazadas)
    assert resumen["acquiring"]["rejected_rows"] == 0
    assert resumen["legacy"]["rejected_rows"] == 137
    assert "DUPLICATE_TXN_ID" in resumen["legacy"]["rejection_reasons"]


# ── Contra la base de verdad ──────────────────────────────────────────────────
@pytest.mark.skipif(not _db_reachable(), reason="sin PostgreSQL en localhost:55433")
def test_glue_reconstruye_exactamente_la_vista_del_core_tras_el_cdc(tmp_path):
    """
    Trafico real -> foto 1 -> mas trafico -> foto 2 -> CDC -> Glue == la vista.

    Entre las dos fotos hay cobros nuevos (I) e intenciones de QR que se pagan
    despues de la primera foto (U). Glue tiene que acabar con exactamente lo que
    la vista devuelve en la base en ese momento: ni una fila de mas, ni un
    importe distinto.
    """
    import psycopg
    from fastapi.testclient import TestClient

    import dms_emulator as dms
    import generate_traffic as gt
    from app.main import app

    with psycopg.connect(CONNINFO) as conn:
        conn.execute((REPO_ROOT / "payments/schema/005_demo_portfolio.sql").read_text())

    with TestClient(app) as client:
        def send(m, p, b, h):
            r = client.request(m, p, json=b, headers=h)
            return r.status_code, r.json()

        gt.run(send, {}, count=60, seed=3, online=False)
        # Un QR que queda pendiente en la foto 1 y se paga despues: el U.
        client.post("/v1/intents", json={"intent_reference": "PIDMSU0001",
                                         "terminal_id": "TID00004", "amount_minor": 2750})
        foto1 = dms.snapshot_from_db(CONNINFO)
        client.post("/v1/intents/PIDMSU0001/confirm", json={"channel": "QR"})
        gt.run(send, {}, count=40, seed=4, online=False)

        # Dos cobros que YA estaban en la foto 1 se devuelven despues: uno entero
        # y otro a medias. Es el U sobre una fila antigua que un incremental por
        # fecha de creacion no veria nunca.
        from app import auth
        with psycopg.connect(CONNINFO) as conn:
            pk = conn.execute("SELECT id FROM acquiring.merchants WHERE merchant_id = %s",
                              ("MID000000020001",)).fetchone()[0]
            clave = auth.create_api_key(conn, pk, "test-refund")
            capturados = [r[0] for r in conn.execute(
                """SELECT pi.intent_reference FROM acquiring.payment_intents pi
                   JOIN acquiring.charges c ON c.payment_intent_id = pi.id
                   WHERE pi.merchant_id = %s AND c.status = 'CAPTURED'
                     AND c.amount_refunded_minor = 0 AND c.amount_captured_minor > 200
                   ORDER BY c.id LIMIT 2""", (pk,)).fetchall()]
        assert len(capturados) == 2, "sin cobros que devolver en la foto 1"
        h = {"Authorization": f"Bearer {clave}"}
        assert client.post("/v1/refunds", json={"payment_intent": capturados[0]},
                           headers=h).status_code == 201
        assert client.post("/v1/refunds", json={"payment_intent": capturados[1],
                                                "amount_minor": 100},
                           headers=h).status_code == 201
    foto2 = dms.snapshot_from_db(CONNINFO)

    ahora = datetime.now(timezone.utc)
    dms.write_layout(tmp_path, foto1, [(ahora + timedelta(seconds=30), dms.cdc_delta(foto1, foto2))],
                     load_ts=ahora)
    raw, _ = _leer(tmp_path)
    glue = raw[raw["source"] == "acquiring"].set_index("transaction_id")

    with psycopg.connect(CONNINFO) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT transaction_id, customer_id, mcc, terminal_id, payment_method, status,
                   response_code, rrn, to_char(amount, 'FM999999990.00') AS amount,
                   to_char(fee_amount, 'FM999999990.00') AS fee_amount,
                   to_char(net_amount, 'FM999999990.00') AS net_amount,
                   transaction_date::text AS transaction_date
            FROM acquiring.v_transactions_for_ingestion
            """)
        vista = pd.DataFrame(cur.fetchall(), columns=[d.name for d in cur.description]
                             ).set_index("transaction_id")

    assert set(glue.index) == set(vista.index), "Glue y la vista no tienen los mismos cobros"
    for col in vista.columns:
        a = glue.loc[vista.index, col].astype("string").fillna("")
        b = vista[col].astype("string").fillna("")
        distintos = a[a != b]
        assert distintos.empty, f"{col} difiere en {len(distintos)} cobros: {distintos.head(3).to_dict()}"

    # Y el U llego: el QR pagado despues de la foto 1 esta, capturado.
    assert glue.loc["ch_PIDMSU0001_1", "status"] == "CAPTURED"

    # Las devoluciones de cobros de la foto 1: la entera es REFUNDED, la parcial
    # sigue CAPTURED. Y en la foto 1 los dos estaban CAPTURED.
    devuelto = glue.loc[glue.index.str.startswith(f"ch_{capturados[0]}")]
    parcial = glue.loc[glue.index.str.startswith(f"ch_{capturados[1]}")]
    assert devuelto["status"].tolist() == ["REFUNDED"]
    assert parcial["status"].tolist() == ["CAPTURED"]
    assert "REFUNDED" not in set(foto1["charges"]["status"])
