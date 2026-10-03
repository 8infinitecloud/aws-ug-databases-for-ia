"""
El generador de trafico contra la API de verdad y un PostgreSQL real.

Comprueba lo que importa para la demo: que no provoca un solo 5xx, y que lo que
deja en la base tiene la variedad que los labs necesitan — varios comercios,
los tres canales, declinaciones con su codigo, 3DS y abandonos.

Misma base que test_api.py. Sin base, el modulo se salta.
"""

from __future__ import annotations

import sys

import pytest

from tests.test_api import REPO_ROOT, _db_reachable, _query

sys.path.insert(0, str(REPO_ROOT / "scripts"))

pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="no hay PostgreSQL con el esquema en localhost:55433 (ver test_api.py)",
)


def _aplicar_cartera():
    import psycopg

    sql = (REPO_ROOT / "payments/schema/005_demo_portfolio.sql").read_text()
    with psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    ) as conn:
        conn.execute(sql)


@pytest.fixture(scope="module")
def corrida():
    """Una corrida de 400 operaciones con semilla fija, compartida por las pruebas."""
    import psycopg
    from fastapi.testclient import TestClient

    import generate_traffic as gt
    from app import auth
    from app.main import app

    _aplicar_cartera()
    claves = {}
    with psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    ) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT merchant_id, id FROM acquiring.merchants")
            pks = dict(cur.fetchall())
        for c in gt.CARTERA:
            if c.online:
                claves[c.mid] = auth.create_api_key(conn, pks[c.mid], "test")
        claves[gt.SIN_KYC] = auth.create_api_key(conn, pks[gt.SIN_KYC], "test")

    antes = _query("SELECT coalesce(max(id), 0) AS n FROM acquiring.charges")[0]["n"]
    with TestClient(app) as client:
        def send(method, path, body, headers):
            r = client.request(method, path, json=body, headers=headers)
            return r.status_code, r.json()

        resultado = gt.run(send, claves, count=400, seed=2026)

    cobros = _query(
        """
        SELECT c.channel::text AS channel, c.response_code, m.merchant_id, m.mcc
        FROM acquiring.charges c JOIN acquiring.merchants m ON m.id = c.merchant_id
        WHERE c.id > %s
        """,
        (antes,),
    )
    return resultado, cobros


def test_ni_un_5xx(corrida):
    resultado, _ = corrida
    assert resultado.get("error_5xx", 0) == 0, resultado


def test_los_terminales_del_generador_existen_en_la_cartera(corrida):
    import generate_traffic as gt

    tids = {t for c in gt.CARTERA for t in c.terminales}
    en_base = {f["terminal_id"] for f in _query(
        "SELECT terminal_id FROM acquiring.terminals WHERE terminal_id = ANY(%s)", (list(tids),)
    )}
    assert tids == en_base
    # El Core2 real queda fuera del trafico simulado.
    assert "TID00001" not in tids


def test_hay_los_tres_canales(corrida):
    _, cobros = corrida
    assert {c["channel"] for c in cobros} == {"QR", "CARD_PRESENT", "CARD_NOT_PRESENT"}


def test_hay_variedad_de_comercios_y_mcc(corrida):
    _, cobros = corrida
    assert len({c["merchant_id"] for c in cobros}) >= 8
    assert {"6051", "5816"} <= {c["mcc"] for c in cobros}  # los de riesgo alto


def test_hay_declinaciones_con_cada_codigo(corrida):
    _, cobros = corrida
    assert {"00", "05", "51", "54"} <= {c["response_code"] for c in cobros}


def test_hay_3ds_abandonos_reintentos_y_el_rechazo_por_kyc(corrida):
    resultado, _ = corrida
    for clave in ("online_3ds_ok", "online_abandonado", "qr_abandonado", "online_reintento",
                  "devolucion_total"):
        assert resultado.get(clave, 0) > 0, (clave, resultado)
    # P_SIN_KYC es 1%: en 400 pasos con esta semilla aparece. Si cambia la
    # semilla puede no hacerlo, y por eso solo se comprueba el codigo.
    for clave, n in resultado.items():
        if clave.startswith("sin_kyc_"):
            assert clave == "sin_kyc_409", resultado


def test_la_mayoria_se_aprueba(corrida):
    _, cobros = corrida
    aprobados = sum(1 for c in cobros if c["response_code"] == "00")
    assert 0.75 < aprobados / len(cobros) < 0.97


def test_el_card_testing_declina_casi_todo_y_dispara_el_scoring():
    """El ataque pasa por la API real y el scorer del Lab 4 lo reconoce."""
    import importlib.util

    import generate_traffic as gt
    from fastapi.testclient import TestClient

    from app import auth
    from app.main import app

    import psycopg

    with psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    ) as conn:
        pk = conn.execute("SELECT id FROM acquiring.merchants WHERE merchant_id = %s",
                          ("MID000000020010",)).fetchone()[0]
        claves = {"MID000000020010": auth.create_api_key(conn, pk, "test")}

    eventos = []
    from app import main as api
    original = api.publisher.publish
    api.publisher.publish = lambda event, device_serial=None: eventos.append(event) or True
    try:
        with TestClient(app) as client:
            def send(method, path, body, headers):
                r = client.request(method, path, json=body, headers=headers)
                return r.status_code, r.json()
            res = gt.run(send, claves, ataque=True, seed=1)
    finally:
        api.publisher.publish = original

    declinadas = sum(v for k, v in res.items() if k.startswith("ataque_") and k != "ataque_00")
    assert declinadas >= 12 and res["ataque_00"] >= 1, res

    spec = importlib.util.spec_from_file_location(
        "rs_ataque", REPO_ROOT / "labs/04-streaming-online-insight/risk_scorer.py")
    rs = importlib.util.module_from_spec(spec)
    # Las dataclasses del scorer buscan su modulo en sys.modules al definirse.
    sys.modules["rs_ataque"] = rs
    spec.loader.exec_module(rs)
    c = rs.Counters(transactions=len(eventos), amount=sum(e["amount"] for e in eventos),
                    declines=sum(1 for e in eventos if e["response_code"] != "00"))
    reglas = {a.rule for a in rs.score(eventos[-1], c).alerts}
    assert "DECLINE_RATIO" in reglas
    assert {rs.partition_key(e) for e in eventos} == {"M#MID000000020010"}


def test_la_flota_virtual_late_con_el_contrato_del_firmware():
    """
    La telemetria de los terminales virtuales: forma, topic y los dos perfiles
    con problemas que los labs tienen que poder encontrar.
    """
    import psycopg
    from fastapi.testclient import TestClient

    import generate_traffic as gt
    from app.main import app

    _aplicar_cartera()
    latidos: list[tuple[str, dict]] = []
    antes = _query("SELECT coalesce(max(id), 0) AS n FROM acquiring.payment_intents")[0]["n"]
    with TestClient(app) as client:
        def send(method, path, body, headers):
            r = client.request(method, path, json=body, headers=headers)
            return r.status_code, r.json()
        res = gt.run(send, {}, count=600, seed=11, online=False,
                     publish=lambda topic, m: latidos.append((topic, m)), latido_cada=20)

    assert res.get("error_5xx", 0) == 0, res
    topics = {t for t, _ in latidos}
    assert all(t.startswith("sim-tid") and t.endswith("/telemetry") for t in topics)
    obligatorios = {"type", "schema_version", "terminal_id", "battery_pct", "rssi_dbm",
                    "http_requests", "http_timeouts", "http_latency_ms_avg", "qr_shown"}
    assert all(obligatorios <= m.keys() for _, m in latidos)

    def de(tid):
        return [m for t, m in latidos if t == f"sim-{tid.lower()}/telemetry"]

    # El taxi con mala cobertura: senal baja y timeouts que nadie mas tiene.
    malo, bueno = de("TID00006"), de("TID00004")
    media = lambda xs, k: sum(x[k] for x in xs) / len(xs)  # noqa: E731
    assert media(malo, "rssi_dbm") < -78 < media(bueno, "rssi_dbm")
    assert sum(m["http_timeouts"] for m in malo) > 0
    assert sum(m["http_timeouts"] for m in bueno) == 0

    # Y sus timeouts estan en la base: intenciones abandonadas de ese terminal.
    abandonadas = _query(
        """
        SELECT count(*) AS n FROM acquiring.payment_intents pi
        JOIN acquiring.terminals t ON t.id = pi.terminal_id
        WHERE pi.id > %s AND t.terminal_id = 'TID00006' AND pi.status = 'CANCELED'
        """, (antes,))[0]["n"]
    assert abandonadas >= sum(m["http_timeouts"] for m in malo)

    # El de la bateria agotada deja de latir: los demas siguen.
    assert len(de("TID00007")) < len(de("TID00004"))
    assert de("TID00007")[-1]["battery_pct"] <= 5


def test_las_devoluciones_del_generador_salen_bien(corrida):
    """Ninguna devolucion falla: el generador solo devuelve ventas que cobro."""
    resultado, _ = corrida
    assert resultado.get("devolucion_total", 0) + resultado.get("devolucion_parcial", 0) > 0
    assert not [k for k in resultado if k.startswith("devolucion_") and k[-3:].isdigit()], resultado
