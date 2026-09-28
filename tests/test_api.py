"""
Pruebas de integracion de la API de adquirencia contra un PostgreSQL real.

No son unitarias a proposito: lo que hay que comprobar es que el ledger cuadra,
que la idempotencia impide el doble cobro y que una declinacion devuelve 200 con
su codigo. Nada de eso se ve con la base simulada, porque la garantia de que
`net = gross - fee` es una constraint de la base, no codigo de Python.

Levantar la base antes:

    docker run -d --name fincorp-api-test -e POSTGRES_PASSWORD=demo \
        -e POSTGRES_DB=acquiring -p 55433:5432 postgres:16.3-alpine
    for f in payments/schema/*.sql; do
        docker exec -i fincorp-api-test psql -U postgres -d acquiring -q < "$f"
    done

Sin base, el modulo entero se salta.
"""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# La configuracion se lee al importar el modulo, asi que el entorno va antes.
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_PORT", "55433")
os.environ.setdefault("DB_NAME", "acquiring")
os.environ.setdefault("DB_USER", "postgres")
os.environ.setdefault("DB_PASSWORD", "demo")
os.environ.setdefault("DEMO_CVV", "123")
# Sin IOT_ENDPOINT los eventos van al log: la prueba no necesita IoT Core.
os.environ.pop("IOT_ENDPOINT", None)

pytest.importorskip("fastapi")
pytest.importorskip("psycopg")

sys.path.insert(0, str(REPO_ROOT / "payments" / "backend"))

TERMINAL = "TID00001"
CARD = "demo_card_4242"


def _db_reachable() -> bool:
    try:
        import psycopg

        with psycopg.connect(
            "host=localhost port=55433 dbname=acquiring user=postgres "
            "password=demo connect_timeout=3"
        ) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM acquiring.merchants LIMIT 1")
                return cur.fetchone() is not None
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="no hay PostgreSQL con el esquema en localhost:55433 (ver docstring)",
)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture
def ref() -> str:
    """Una referencia distinta por prueba, como la generaria cada cobro del POS."""
    return f"PI{uuid.uuid4().hex[:12].upper()}"


def _query(sql: str, params: tuple = ()):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    ) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def _crear(client, ref: str, amount: int = 1850, **kw) -> dict:
    body = {
        "intent_reference": ref,
        "terminal_id": TERMINAL,
        "amount_minor": amount,
        "currency_code": "PEN",
        **kw,
    }
    return client.post("/v1/intents", json=body, headers={"Idempotency-Key": f"k-{ref}"})


def _confirmar(client, ref: str, cvv: str = "123", key: str | None = None):
    return client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "CARD_PRESENT", "card_token": CARD, "cvv": cvv},
        headers={"Idempotency-Key": key or f"c-{ref}"},
    )


# ── Salud ────────────────────────────────────────────────────────────────────
def test_health_reporta_la_base_y_el_estado_de_iot(client):
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["database"] == "ok"
    assert body["status"] == "ok"
    # Sin IOT_ENDPOINT el servicio funciona y lo dice, en vez de fallar.
    assert body["iot"] == "not_configured"


# ── Intenciones ──────────────────────────────────────────────────────────────
def test_crear_intencion_devuelve_201_con_el_comercio(client, ref):
    r = _crear(client, ref)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["intent_reference"] == ref
    assert body["status"] == "REQUIRES_CONFIRMATION"
    assert body["amount_minor"] == 1850
    assert body["merchant_name"] == "Smart Coffee"


def test_lineas_que_no_suman_el_importe_se_rechazan(client, ref):
    r = _crear(
        client,
        ref,
        amount=1850,
        line_items=[{"sku": "ESP", "name": "Espresso", "quantity": 1, "unit_price_minor": 900}],
    )
    # 422: lo para Pydantic en el borde, antes de tocar la base.
    assert r.status_code == 422
    assert "900" in r.text


def test_terminal_inexistente_da_404(client, ref):
    r = client.post(
        "/v1/intents",
        json={
            "intent_reference": ref,
            "terminal_id": "TID99999",
            "amount_minor": 500,
            "currency_code": "PEN",
        },
        headers={"Idempotency-Key": f"k-{ref}"},
    )
    assert r.status_code == 404
    assert "TID99999" in r.json()["error"]


def test_leer_intencion_devuelve_lo_que_la_web_del_qr_necesita(client, ref):
    _crear(client, ref, amount=2400)
    r = client.get(f"/v1/intents/{ref}")
    assert r.status_code == 200
    body = r.json()
    assert body["amount_minor"] == 2400
    assert body["merchant_name"] == "Smart Coffee"


def test_leer_intencion_inexistente_da_404(client):
    assert client.get("/v1/intents/PINOEXISTE").status_code == 404


# ── El cobro y el ledger ─────────────────────────────────────────────────────
def test_cobro_aprobado_trae_los_data_elements_de_iso8583(client, ref):
    _crear(client, ref)
    r = _confirmar(client, ref)
    assert r.status_code == 200, r.text
    body = r.json()

    assert body["status"] == "CAPTURED"
    assert body["response_code"] == "00"
    assert body["auth_code"] is not None          # DE 38
    assert body["rrn"] is not None                # DE 37, la referencia del reclamo
    assert body["stan"] is not None               # DE 11
    assert body["charge_reference"] is not None


def test_el_mdr_se_calcula_con_la_tarifa_del_comercio(client, ref):
    """MDR 299 bps + 30 fijos sobre 1850 = 55 + 30 = 85; neto 1765."""
    _crear(client, ref, amount=1850)
    body = _confirmar(client, ref).json()

    esperado_fee = round(1850 * 299 / 10_000) + 30
    assert body["fee_minor"] == esperado_fee
    assert body["net_minor"] == 1850 - esperado_fee


def test_el_asiento_cuadra_en_la_base(client, ref):
    """La constraint ledger_net_equals_gross_minus_fee es la que lo garantiza."""
    _crear(client, ref, amount=5000)
    _confirmar(client, ref)

    filas = _query(
        """
        SELECT bt.gross_minor, bt.fee_minor, bt.net_minor, bt.status
        FROM acquiring.balance_transactions bt
        JOIN acquiring.charges c ON c.id = bt.charge_id
        JOIN acquiring.payment_intents pi ON pi.id = c.payment_intent_id
        WHERE pi.intent_reference = %s
        """,
        (ref,),
    )
    assert len(filas) == 1
    fila = filas[0]
    assert fila["net_minor"] == fila["gross_minor"] - fila["fee_minor"]
    assert fila["gross_minor"] == 5000
    assert fila["status"] == "PENDING"   # se liquida tras payout_delay_days


def test_cvv_incorrecto_declina_con_200_y_codigo_05(client, ref):
    """
    Una declinacion no es un error HTTP.

    El POS necesita el response_code para decidir que pinta en pantalla; un 4xx
    le quitaria el dato.
    """
    _crear(client, ref)
    r = _confirmar(client, ref, cvv="999")

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response_code"] == "05"
    assert body["status"] != "CAPTURED"
    assert body["decline_reason"]
    assert body["auth_code"] is None


def test_una_declinacion_no_deja_asiento_en_el_ledger(client, ref):
    _crear(client, ref)
    _confirmar(client, ref, cvv="999")

    filas = _query(
        """
        SELECT bt.id FROM acquiring.balance_transactions bt
        JOIN acquiring.charges c ON c.id = bt.charge_id
        JOIN acquiring.payment_intents pi ON pi.id = c.payment_intent_id
        WHERE pi.intent_reference = %s
        """,
        (ref,),
    )
    assert filas == []


# ── Idempotencia ─────────────────────────────────────────────────────────────
def test_misma_clave_no_cobra_dos_veces(client, ref):
    """El caso que importa: el POS pierde la respuesta y reintenta."""
    _crear(client, ref, amount=3000)
    primera = _confirmar(client, ref, key=f"reintento-{ref}")
    segunda = _confirmar(client, ref, key=f"reintento-{ref}")

    assert primera.status_code == segunda.status_code == 200
    # Misma respuesta, no un cobro nuevo: mismo RRN y mismo charge.
    assert primera.json()["rrn"] == segunda.json()["rrn"]
    assert primera.json()["charge_reference"] == segunda.json()["charge_reference"]

    cobros = _query(
        """
        SELECT c.id FROM acquiring.charges c
        JOIN acquiring.payment_intents pi ON pi.id = c.payment_intent_id
        WHERE pi.intent_reference = %s AND c.status = 'CAPTURED'
        """,
        (ref,),
    )
    assert len(cobros) == 1, "se cobro dos veces"


def test_confirmar_dos_veces_sin_clave_tampoco_cobra_dos_veces(client, ref):
    """
    La defensa de ultimo recurso: la intencion ya esta en SUCCEEDED.

    Sin cabecera de idempotencia el cobro sigue protegido, porque confirm_intent
    comprueba el estado de la intencion antes de autorizar.
    """
    _crear(client, ref, amount=1200)
    client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "CARD_PRESENT", "card_token": CARD, "cvv": "123"},
    )
    client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "CARD_PRESENT", "card_token": CARD, "cvv": "123"},
    )

    cobros = _query(
        """
        SELECT c.id FROM acquiring.charges c
        JOIN acquiring.payment_intents pi ON pi.id = c.payment_intent_id
        WHERE pi.intent_reference = %s AND c.status = 'CAPTURED'
        """,
        (ref,),
    )
    assert len(cobros) == 1


def test_misma_clave_con_cuerpo_distinto_da_409(client, ref):
    _crear(client, ref)
    _confirmar(client, ref, key=f"choque-{ref}")

    # Misma clave, otra tarjeta: el fingerprint no coincide.
    otra = client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "CARD_PRESENT", "card_token": "demo_card_9999", "cvv": "123"},
        headers={"Idempotency-Key": f"choque-{ref}"},
    )
    assert otra.status_code == 409


# ── El canal QR ──────────────────────────────────────────────────────────────
def test_el_qr_confirma_sin_credencial(client, ref):
    """La web del QR no manda CVV: el canal discrimina el cuerpo."""
    _crear(client, ref, amount=1500)
    r = client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "QR"},
        headers={"Idempotency-Key": f"qr-{ref}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["response_code"] == "00"
    assert body["status"] == "CAPTURED"

    canal = _query(
        "SELECT channel FROM acquiring.payment_intents WHERE intent_reference = %s",
        (ref,),
    )
    assert canal[0]["channel"] == "QR"


# ── Coherencia entre el evento y las reglas del Lab 2 ────────────────────────
# Este bloque existe porque el fallo que cubre ya ocurrio: el evento salia con
# `payment_method: null` y el Lab 2 habria rechazado el 100% de las filas del
# stream. Las dos piezas viven en carpetas distintas y nada las ataba.
def _reglas_del_lab2():
    """Carga las constantes del job de Glue, que son el contrato del consumidor."""
    import importlib.util

    ruta = REPO_ROOT / "labs/02-transformation-quality-catalog/glue_job.py"
    spec = importlib.util.spec_from_file_location("glue_job_ref", ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def eventos(monkeypatch):
    """Captura lo que se habria publicado en IoT Core."""
    from app import main as api

    capturados: list[dict] = []
    monkeypatch.setattr(
        api.publisher, "publish", lambda event, device_serial=None: capturados.append(event) or True
    )
    return capturados


@pytest.mark.parametrize(
    "cuerpo, metodo_esperado",
    [
        ({"channel": "CARD_PRESENT", "card_token": CARD, "cvv": "123"}, "CARD_PRESENT"),
        ({"channel": "QR"}, "QR"),
    ],
)
def test_el_evento_publica_un_payment_method_que_el_lab2_acepta(
    client, ref, eventos, cuerpo, metodo_esperado
):
    lab2 = _reglas_del_lab2()

    _crear(client, ref, amount=1850)
    r = client.post(
        f"/v1/intents/{ref}/confirm", json=cuerpo, headers={"Idempotency-Key": f"e-{ref}"}
    )
    assert r.status_code == 200, r.text

    assert len(eventos) == 1, "no se publico el evento"
    evento = eventos[0]
    assert evento["payment_method"] == metodo_esperado
    # La regla que lo rechazaria: invalid_method en glue_job.apply_data_quality.
    assert evento["payment_method"] in lab2.VALID_PAYMENT_METHODS
    assert evento["status"] in lab2.VALID_STATUSES
    assert evento["currency"] in lab2.VALID_CURRENCIES


def test_el_evento_de_una_declinacion_tambien_es_valido(client, ref, eventos):
    lab2 = _reglas_del_lab2()

    _crear(client, ref)
    _confirmar(client, ref, cvv="999")

    assert len(eventos) == 1
    evento = eventos[0]
    assert evento["event_type"] == "payment.declined"
    assert evento["payment_method"] in lab2.VALID_PAYMENT_METHODS
    assert evento["status"] in lab2.VALID_STATUSES
    assert evento["response_code"] != lab2.RESPONSE_CODE_APPROVED


def test_los_importes_del_evento_cuadran_como_los_espera_el_lab2(client, ref, eventos):
    """
    El evento publica soles, no centimos, y el Lab 2 comprueba
    amount - fee - net ~ 0 con LEDGER_TOLERANCE.
    """
    lab2 = _reglas_del_lab2()

    _crear(client, ref, amount=1850)
    _confirmar(client, ref)

    evento = eventos[0]
    assert evento["amount"] == 18.50
    descuadre = abs(evento["amount"] - evento["fee_amount"] - evento["net_amount"])
    assert descuadre <= lab2.LEDGER_TOLERANCE


# ── La pagina de pago del QR ─────────────────────────────────────────────────
def _pagina() -> str:
    return (REPO_ROOT / "payments/web/index.html").read_text(encoding="utf-8")


def test_la_pagina_se_sirve_como_html_y_no_se_cachea(client, ref):
    _crear(client, ref, amount=1850)
    r = client.get(f"/pay/{ref}")

    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    # El cobro cambia de estado al pagarlo: una pagina cacheada mostraria un
    # importe pendiente que ya no lo esta.
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["x-content-type-options"] == "nosniff"


def test_la_pagina_no_lleva_la_referencia_inyectada(client, ref):
    """
    Es estatica de verdad: la referencia la lee de su propia URL.

    Si se inyectara habria que escaparla, y una referencia con HTML dentro seria
    un XSS en una pagina de pagos.
    """
    _crear(client, ref)
    assert ref not in client.get(f"/pay/{ref}").text


def test_la_pagina_avisa_de_que_es_una_demo(client, ref):
    """
    El aviso no es decorativo: es un requisito explicito del proyecto.

    Quien escanee el QR desde el publico tiene que ver, sin leer nada mas, que
    no se le esta cobrando dinero.
    """
    _crear(client, ref)
    html = client.get(f"/pay/{ref}").text
    assert "DEMO" in html
    assert "no se cobra dinero real" in html


def test_una_referencia_inexistente_devuelve_la_pagina_igual(client):
    """
    200 y no 404: la pagina es estatica y el error lo pinta ella.

    Devolver 404 aqui obligaria a servir dos documentos distintos y no aportaria
    nada, porque quien abre la URL es un movil que va a llamar a la API de todos
    modos.
    """
    assert client.get("/pay/PINOEXISTE").status_code == 200


def test_el_javascript_de_la_pagina_es_valido():
    """
    Un error de sintaxis dejaria la pagina en blanco sin que nada fallara.

    Se comprueba con node si esta; si no, la prueba se salta en vez de dar una
    falsa sensacion de cobertura.
    """
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no esta instalado")

    bloques = re.findall(r"<script>(.*?)</script>", _pagina(), re.DOTALL)
    assert bloques, "la pagina no tiene script"

    for js in bloques:
        # --check analiza sin ejecutar: no toca la red ni el DOM.
        proc = subprocess.run(
            [node, "--check", "-"], input=js, capture_output=True, text=True
        )
        assert proc.returncode == 0, proc.stderr


def test_la_pagina_llama_a_las_rutas_que_la_api_expone():
    """
    Ata la pagina a la API.

    Si alguien renombra una ruta, el fallo aparece aqui y no en el movil de
    alguien del publico durante la charla.
    """
    html = _pagina()
    assert '"/v1/intents/" + encodeURIComponent(referencia)' in html
    assert '/confirm' in html
    assert '"Idempotency-Key"' in html, "sin clave, un doble toque cobraria dos veces"
    assert '{ channel: "QR" }' in html



def test_el_evento_lleva_el_mcc_del_comercio(client, ref, eventos):
    """
    Sin MCC, una fila del stream llega al lago sin la señal de riesgo.

    El Lab 2 lo usa como columna, Athena agrupa por él, y el scoring online del
    Lab 4 lo necesita para distinguir una casa de cambio de una cafetería. Es el
    mismo tipo de hueco que tuvo `payment_method`.
    """
    _crear(client, ref)
    _confirmar(client, ref)

    evento = eventos[0]
    # Smart Coffee está afiliado con MCC 5812 (restaurantes), que es lo que
    # siembra 002_seed.sql.
    assert evento["mcc"] == "5812"
    assert evento["risk_level"] in {"LOW", "MEDIUM", "HIGH"}
