"""
El canal online, contra un PostgreSQL real, como test_api.py.

Sigue el ciclo de vida de un PaymentIntent de Stripe: nace pidiendo medio de
pago, se confirma con un PaymentMethod de prueba, puede pedir 3DS, y tras una
declinacion vuelve a pedir medio de pago en vez de morir.

Misma base que test_api.py (ver su docstring para levantarla). Sin base, el
modulo entero se salta.
"""

from __future__ import annotations

import uuid
from urllib.parse import parse_qs, urlparse

import pytest

# El entorno y la deteccion de la base son los de test_api: importarlo los
# aplica antes de que app.main lea la configuracion.
from tests.test_api import REPO_ROOT, _db_reachable, _query  # noqa: F401

pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="no hay PostgreSQL con el esquema en localhost:55433 (ver test_api.py)",
)

SMART_COFFEE = "MID000000012345"
OTRO_COMERCIO = "MID000000099901"


def _conectar():
    import psycopg

    return psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    )


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="module")
def claves():
    """Una clave de escritura por comercio, y una de solo lectura."""
    from app import auth

    with _conectar() as conn:
        with conn.cursor() as cur:
            # Un segundo comercio, para comprobar que uno no toca lo del otro.
            cur.execute(
                """
                INSERT INTO acquiring.merchants
                    (merchant_id, legal_name, trade_name, mcc, status, risk_level,
                     kyc_completed_at, charges_enabled)
                VALUES (%s, 'Tienda Online Prueba S.A.C.', 'Tienda Prueba', '5732',
                        'ACTIVE', 'LOW', NOW(), TRUE)
                ON CONFLICT (merchant_id) DO NOTHING
                """,
                (OTRO_COMERCIO,),
            )
            cur.execute("SELECT id, merchant_id FROM acquiring.merchants WHERE merchant_id IN (%s, %s)",
                        (SMART_COFFEE, OTRO_COMERCIO))
            pk = {mid: id_ for id_, mid in cur.fetchall()}
        result = {
            "smart": auth.create_api_key(conn, pk[SMART_COFFEE], "tests"),
            "otro": auth.create_api_key(conn, pk[OTRO_COMERCIO], "tests"),
            "lectura": auth.create_api_key(conn, pk[SMART_COFFEE], "tests-ro",
                                           scopes=["read:charges"]),
        }
    return result


@pytest.fixture
def ref() -> str:
    return f"PI{uuid.uuid4().hex[:12].upper()}"


@pytest.fixture
def eventos(monkeypatch):
    """Captura lo que se habria publicado en IoT Core, con su topic."""
    from app import main as api

    capturados: list[tuple[dict, str | None]] = []
    monkeypatch.setattr(
        api.publisher, "publish",
        lambda event, device_serial=None: capturados.append((event, device_serial)) or True,
    )
    return capturados


def _h(clave: str, idem: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {clave}"}
    if idem:
        h["Idempotency-Key"] = idem
    return h


def _crear(client, ref, clave, amount=12990, **kw):
    body = {"intent_reference": ref, "amount_minor": amount, "currency_code": "PEN", **kw}
    return client.post("/v1/intents", json=body, headers=_h(clave, f"k-{ref}"))


def _pagar(client, ref, clave, pm="pm_card_visa", idem=None):
    return client.post(
        f"/v1/intents/{ref}/confirm",
        json={"channel": "CARD_NOT_PRESENT", "payment_method": pm},
        headers=_h(clave, idem or f"c-{ref}-{pm}"),
    )


def _intent(ref):
    return _query(
        "SELECT * FROM acquiring.payment_intents WHERE intent_reference = %s", (ref,)
    )[0]


def _cobros(ref):
    return _query(
        """
        SELECT c.charge_reference, c.status, c.channel, c.response_code, c.rrn
        FROM acquiring.charges c
        JOIN acquiring.payment_intents pi ON pi.id = c.payment_intent_id
        WHERE pi.intent_reference = %s ORDER BY c.id
        """,
        (ref,),
    )


# ── Autenticacion del comercio ───────────────────────────────────────────────
def test_sin_terminal_ni_clave_da_401(client, ref):
    r = client.post("/v1/intents", json={"intent_reference": ref, "amount_minor": 100})
    assert r.status_code == 401, r.text


def test_una_clave_inventada_da_401(client, ref):
    r = _crear(client, ref, "sk_demo_estonoexisteniexistio")
    assert r.status_code == 401


def test_una_clave_de_solo_lectura_no_cobra(client, ref, claves):
    r = _crear(client, ref, claves["lectura"])
    assert r.status_code == 403


def test_la_clave_no_se_guarda_en_claro(claves):
    filas = _query("SELECT key_prefix, key_hash FROM acquiring.merchant_api_keys")
    guardado = {f["key_prefix"] for f in filas} | {f["key_hash"] for f in filas}
    assert claves["smart"] not in guardado
    assert claves["smart"][:12] in guardado  # solo el prefijo visible


# ── El ciclo de vida ─────────────────────────────────────────────────────────
def test_una_intencion_online_nace_pidiendo_medio_de_pago(client, ref, claves):
    r = _crear(client, ref, claves["smart"])
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "REQUIRES_PAYMENT_METHOD"
    assert _intent(ref)["terminal_id"] is None


def test_pm_card_visa_cobra_y_asienta(client, ref, claves, eventos):
    _crear(client, ref, claves["smart"])
    r = _pagar(client, ref, claves["smart"])

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "CAPTURED"
    assert body["response_code"] == "00"
    assert body["channel"] == "CARD_NOT_PRESENT"
    assert body["fee_minor"] + body["net_minor"] == 12990
    assert _intent(ref)["status"] == "SUCCEEDED"

    # El evento sale para el Lab 4 aunque no haya dispositivo: va al topic online.
    assert len(eventos) == 1
    evento, serial = eventos[0]
    assert serial is None
    assert evento["payment_method"] == "CARD_NOT_PRESENT"


@pytest.mark.parametrize(
    "pm, codigo",
    [
        ("pm_card_visa_chargeDeclined", "05"),
        ("pm_card_visa_chargeDeclinedInsufficientFunds", "51"),
        ("pm_card_chargeDeclinedExpiredCard", "54"),
    ],
)
def test_cada_token_de_prueba_fuerza_su_declinacion(client, ref, claves, pm, codigo):
    _crear(client, ref, claves["smart"])
    r = _pagar(client, ref, claves["smart"], pm=pm)

    assert r.status_code == 200, r.text
    assert r.json()["response_code"] == codigo
    assert r.json()["status"] == "FAILED"
    intent = _intent(ref)
    # Como en Stripe: no muere, vuelve a pedir medio de pago.
    assert intent["status"] == "REQUIRES_PAYMENT_METHOD"
    assert intent["last_error_code"] == codigo


def test_tras_una_declinacion_se_reintenta_sobre_la_misma_intencion(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"], pm="pm_card_visa_chargeDeclined")
    _pagar(client, ref, claves["smart"], pm="pm_card_visa_chargeDeclinedInsufficientFunds")
    r = _pagar(client, ref, claves["smart"], pm="pm_card_visa")

    assert r.json()["status"] == "CAPTURED"
    cobros = _cobros(ref)
    # Un cobro por intento, cada uno con su referencia: antes el segundo rechazo
    # chocaba con la UNIQUE del primero.
    assert [c["status"] for c in cobros] == ["FAILED", "FAILED", "CAPTURED"]
    assert len({c["charge_reference"] for c in cobros}) == 3
    assert _intent(ref)["status"] == "SUCCEEDED"


def test_un_token_que_no_existe_da_404(client, ref, claves):
    _crear(client, ref, claves["smart"])
    r = _pagar(client, ref, claves["smart"], pm="pm_card_inventada")
    assert r.status_code == 404


# ── 3-D Secure ───────────────────────────────────────────────────────────────
def _pedir_3ds(client, ref, claves, return_url="https://tienda.example/vuelta"):
    _crear(client, ref, claves["smart"], return_url=return_url)
    r = _pagar(client, ref, claves["smart"], pm="pm_card_threeDSecure2Required")
    assert r.status_code == 200, r.text
    return r.json()


def test_3ds_deja_la_intencion_esperando_sin_cobro(client, ref, claves, eventos):
    body = _pedir_3ds(client, ref, claves)

    assert body["status"] == "REQUIRES_ACTION"
    assert body["response_code"] is None
    url = body["next_action"]["redirect_to_url"]["url"]
    assert urlparse(url).path == f"/3ds/{ref}"
    assert parse_qs(urlparse(url).query)["t"]
    assert "token" not in body["next_action"]  # el secreto va solo en la URL
    assert _cobros(ref) == []
    assert eventos == []  # sin cobro no hay nada que puntuar


def test_la_pagina_del_banco_simulado_se_sirve(client, ref, claves):
    _pedir_3ds(client, ref, claves)
    r = client.get(f"/3ds/{ref}")
    assert r.status_code == 200
    assert "DEMO" in r.text
    assert ref not in r.text  # nada inyectado: la lee de la URL


def test_3ds_con_token_ajeno_da_403(client, ref, claves):
    _pedir_3ds(client, ref, claves)
    r = client.post(f"/v1/intents/{ref}/authenticate",
                    json={"token": "otro-token-cualquiera", "authenticated": True})
    assert r.status_code == 403
    assert _intent(ref)["status"] == "REQUIRES_ACTION"


def test_3ds_superado_cobra_y_devuelve_a_la_tienda(client, ref, claves, eventos):
    body = _pedir_3ds(client, ref, claves)
    token = parse_qs(urlparse(body["next_action"]["redirect_to_url"]["url"]).query)["t"][0]

    r = client.post(f"/v1/intents/{ref}/authenticate",
                    json={"token": token, "authenticated": True})

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CAPTURED"
    assert r.json()["return_url"] == "https://tienda.example/vuelta"
    intent = _intent(ref)
    assert intent["status"] == "SUCCEEDED"
    assert intent["next_action"] is None
    assert len(eventos) == 1

    # El token es de un solo uso.
    r2 = client.post(f"/v1/intents/{ref}/authenticate",
                     json={"token": token, "authenticated": True})
    assert r2.status_code == 409


def test_3ds_fallido_no_crea_cobro_y_permite_otra_tarjeta(client, ref, claves):
    body = _pedir_3ds(client, ref, claves)
    token = parse_qs(urlparse(body["next_action"]["redirect_to_url"]["url"]).query)["t"][0]

    r = client.post(f"/v1/intents/{ref}/authenticate",
                    json={"token": token, "authenticated": False})

    assert r.json()["status"] == "REQUIRES_PAYMENT_METHOD"
    intent = _intent(ref)
    assert intent["last_error_code"] == "AUTH_3DS"
    assert _cobros(ref) == []
    assert _pagar(client, ref, claves["smart"]).json()["status"] == "CAPTURED"


def test_confirmar_mientras_espera_3ds_da_409(client, ref, claves):
    _pedir_3ds(client, ref, claves)
    r = _pagar(client, ref, claves["smart"], idem=f"otra-{ref}")
    assert r.status_code == 409


def test_return_url_que_no_es_https_se_rechaza(client, ref, claves):
    r = _crear(client, ref, claves["smart"], return_url="javascript:alert(1)")
    assert r.status_code == 422


# ── Quien toca que ───────────────────────────────────────────────────────────
def test_confirmar_una_intencion_online_exige_clave(client, ref, claves):
    _crear(client, ref, claves["smart"])
    r = client.post(f"/v1/intents/{ref}/confirm",
                    json={"channel": "CARD_NOT_PRESENT", "payment_method": "pm_card_visa"})
    assert r.status_code == 401


def test_otro_comercio_no_ve_la_intencion(client, ref, claves):
    _crear(client, ref, claves["smart"])
    r = _pagar(client, ref, claves["otro"])
    assert r.status_code == 404
    assert _cobros(ref) == []


def test_una_intencion_online_no_se_paga_por_qr(client, ref, claves):
    _crear(client, ref, claves["smart"])
    r = client.post(f"/v1/intents/{ref}/confirm", json={"channel": "QR"},
                    headers=_h(claves["smart"]))
    assert r.status_code == 422


def test_una_venta_de_mostrador_no_se_paga_online(client, ref, claves):
    client.post("/v1/intents", json={"intent_reference": ref, "terminal_id": "TID00001",
                                     "amount_minor": 1850})
    r = _pagar(client, ref, claves["smart"])
    assert r.status_code == 422


def test_el_terminal_de_otro_comercio_no_se_puede_usar(client, ref, claves):
    r = _crear(client, ref, claves["otro"], terminal_id="TID00001")
    assert r.status_code == 404


# ── Cancelacion ──────────────────────────────────────────────────────────────
def test_cancelar_y_despues_confirmar_da_409(client, ref, claves):
    _crear(client, ref, claves["smart"])
    r = client.post(f"/v1/intents/{ref}/cancel", json={"cancellation_reason": "abandoned"},
                    headers=_h(claves["smart"]))
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "CANCELED"
    assert _intent(ref)["cancellation_reason"] == "abandoned"

    assert _pagar(client, ref, claves["smart"]).status_code == 409


def test_no_se_cancela_lo_ya_cobrado(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"])
    r = client.post(f"/v1/intents/{ref}/cancel", headers=_h(claves["smart"]))
    assert r.status_code == 409


# ── Lo que llega al lago ─────────────────────────────────────────────────────
def test_cada_paso_deja_su_evento_en_la_misma_transaccion(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"], pm="pm_card_visa_chargeDeclined")
    _pagar(client, ref, claves["smart"])

    tipos = [
        f["event_type"]
        for f in _query(
            "SELECT event_type FROM acquiring.webhook_events WHERE object_id = %s ORDER BY id",
            (ref,),
        )
    ]
    assert tipos == [
        "payment_intent.created",
        "payment_intent.payment_failed",
        "payment_intent.succeeded",
    ]


def test_el_rrn_no_se_repite_y_tiene_12_digitos(client, claves):
    rrns = []
    for _ in range(30):
        r = f"PI{uuid.uuid4().hex[:12].upper()}"
        _crear(client, r, claves["smart"], amount=500)
        rrns.append(_pagar(client, r, claves["smart"]).json()["rrn"])
    assert len(set(rrns)) == 30
    assert all(len(x) == 12 and x.isdigit() for x in rrns)


def test_el_canal_online_pasa_las_reglas_del_lab2(client, ref, claves, eventos):
    from tests.test_api import _reglas_del_lab2

    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"])
    evento, _ = eventos[0]
    assert evento["payment_method"] in _reglas_del_lab2().VALID_PAYMENT_METHODS


def test_el_javascript_de_la_pagina_3ds_es_valido():
    """Como la del QR: un error de sintaxis la dejaria muda sin que nada fallara."""
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no esta instalado")

    html = (REPO_ROOT / "payments/web/3ds.html").read_text(encoding="utf-8")
    bloques = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert bloques
    for js in bloques:
        proc = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr


def test_la_pagina_3ds_llama_a_la_ruta_que_la_api_expone():
    html = (REPO_ROOT / "payments/web/3ds.html").read_text(encoding="utf-8")
    assert '"/v1/intents/" + encodeURIComponent(referencia) + "/authenticate"' in html


# ── Devoluciones ─────────────────────────────────────────────────────────────
def _devolver(client, ref, clave, amount=None, idem=None, reason="requested_by_customer"):
    body = {"payment_intent": ref, "reason": reason}
    if amount is not None:
        body["amount_minor"] = amount
    return client.post("/v1/refunds", json=body, headers=_h(clave, idem or f"r-{uuid.uuid4().hex[:8]}"))


def _estado_en_la_vista(ref):
    return _query(
        "SELECT status FROM acquiring.v_transactions_for_ingestion WHERE transaction_id = %s",
        (f"ch_{ref}_1",),
    )[0]["status"]


def test_una_devolucion_total_deja_el_cobro_como_refunded_en_la_vista(client, ref, claves):
    _crear(client, ref, claves["smart"], amount=5000)
    _pagar(client, ref, claves["smart"])
    r = _devolver(client, ref, claves["smart"])

    assert r.status_code == 201, r.text
    body = r.json()
    assert body["amount_minor"] == 5000 and body["charge_fully_refunded"] is True
    # En el core el cobro sigue CAPTURED (como en Stripe); para el lago, REFUNDED.
    cobro = _query("SELECT status::text, amount_refunded_minor FROM acquiring.charges "
                   "WHERE charge_reference = %s", (f"ch_{ref}_1",))[0]
    assert cobro == {"status": "CAPTURED", "amount_refunded_minor": 5000}
    assert _estado_en_la_vista(ref) == "REFUNDED"
    assert _intent(ref)["status"] == "SUCCEEDED"


def test_el_libro_registra_la_salida_y_la_comision_no_se_devuelve(client, ref, claves):
    _crear(client, ref, claves["smart"], amount=10000)
    cobro = _pagar(client, ref, claves["smart"]).json()
    _devolver(client, ref, claves["smart"])

    asientos = _query(
        """
        SELECT bt.source_type::text, bt.gross_minor, bt.fee_minor, bt.net_minor
        FROM acquiring.balance_transactions bt
        LEFT JOIN acquiring.charges c ON c.id = bt.charge_id
        LEFT JOIN acquiring.refunds r ON r.id = bt.refund_id
        LEFT JOIN acquiring.charges c2 ON c2.id = r.charge_id
        WHERE c.charge_reference = %s OR c2.charge_reference = %s
        ORDER BY bt.id
        """, (f"ch_{ref}_1", f"ch_{ref}_1"))
    assert [a["source_type"] for a in asientos] == ["CHARGE", "REFUND"]
    assert asientos[1]["gross_minor"] == -10000 and asientos[1]["fee_minor"] == 0
    # Lo que le queda al comercio: menos la comision que no se devuelve.
    assert sum(a["net_minor"] for a in asientos) == -cobro["fee_minor"]


def test_dos_parciales_completan_la_devolucion(client, ref, claves):
    _crear(client, ref, claves["smart"], amount=3000)
    _pagar(client, ref, claves["smart"])

    r1 = _devolver(client, ref, claves["smart"], amount=1000)
    assert r1.json()["charge_fully_refunded"] is False
    assert _estado_en_la_vista(ref) == "CAPTURED"   # parcial: sigue capturado

    r2 = _devolver(client, ref, claves["smart"])     # sin importe: lo que queda
    assert r2.json()["amount_minor"] == 2000
    assert r2.json()["refund_reference"] != r1.json()["refund_reference"]
    assert _estado_en_la_vista(ref) == "REFUNDED"


def test_no_se_devuelve_mas_de_lo_capturado(client, ref, claves):
    _crear(client, ref, claves["smart"], amount=1000)
    _pagar(client, ref, claves["smart"])
    assert _devolver(client, ref, claves["smart"], amount=1001).status_code == 409
    _devolver(client, ref, claves["smart"])
    assert _devolver(client, ref, claves["smart"]).status_code == 409   # ya devuelto


def test_un_rechazo_no_se_puede_devolver(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"], pm="pm_card_visa_chargeDeclined")
    assert _devolver(client, ref, claves["smart"]).status_code == 409


def test_la_devolucion_exige_la_clave_del_duenio(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"])
    r = client.post("/v1/refunds", json={"payment_intent": ref})
    assert r.status_code == 401
    assert _devolver(client, ref, claves["otro"]).status_code == 404


def test_una_devolucion_reintentada_no_devuelve_dos_veces(client, ref, claves):
    _crear(client, ref, claves["smart"], amount=4000)
    _pagar(client, ref, claves["smart"])
    a = _devolver(client, ref, claves["smart"], amount=1000, idem=f"r-{ref}")
    b = _devolver(client, ref, claves["smart"], amount=1000, idem=f"r-{ref}")
    assert a.json() == b.json()
    total = _query("SELECT amount_refunded_minor FROM acquiring.charges WHERE charge_reference = %s",
                   (f"ch_{ref}_1",))[0]["amount_refunded_minor"]
    assert total == 1000


def test_la_devolucion_deja_su_evento(client, ref, claves):
    _crear(client, ref, claves["smart"])
    _pagar(client, ref, claves["smart"])
    _devolver(client, ref, claves["smart"])
    eventos = _query("SELECT event_type, object_type FROM acquiring.webhook_events "
                     "WHERE object_id = %s", (f"ch_{ref}_1",))
    assert eventos == [{"event_type": "charge.refunded", "object_type": "charge"}]
