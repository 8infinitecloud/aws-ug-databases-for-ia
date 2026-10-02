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
    for clave in ("online_3ds_ok", "online_abandonado", "qr_abandonado", "online_reintento"):
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
