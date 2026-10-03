"""
Mercado Andino (store/) contra la API de FinCorp de verdad y un PostgreSQL real.

La tienda es el comercio y FinCorp el adquirente: aquí se conectan los dos en
memoria —la tienda manda sus peticiones al TestClient de la API— y se comprueba
lo que importa de un comercio bien hecho: que el total lo pone el servidor y no
el navegador, que la clave nunca sale hacia el cliente, y que cada camino del
banco simulado termina donde tiene que terminar.

Misma base que test_api.py. Sin base, el módulo se salta (y el CI falla, que es
lo que debe pasar: ver ci.yml).
"""

from __future__ import annotations

import sys
from urllib.parse import parse_qs, urlparse

import pytest

from tests.test_api import REPO_ROOT, _db_reachable, _query

pytestmark = pytest.mark.skipif(
    not _db_reachable(),
    reason="no hay PostgreSQL con el esquema en localhost:55433 (ver test_api.py)",
)

# La tienda se carga por ruta y con otro nombre (store_app): su fichero se llama
# app.py, como el paquete del backend, y meter store/ en sys.path haria que
# `import app` encontrara la tienda en vez de la API.
API = "https://api.fincorp.example"


@pytest.fixture(scope="module")
def entorno():
    import psycopg
    from fastapi.testclient import TestClient

    from app import auth
    from app.main import app as api_app

    with psycopg.connect(
        "host=localhost port=55433 dbname=acquiring user=postgres password=demo"
    ) as conn:
        conn.execute((REPO_ROOT / "payments/schema/005_demo_portfolio.sql").read_text())
        pk = conn.execute("SELECT id FROM acquiring.merchants WHERE merchant_id = %s",
                          ("MID000000020012",)).fetchone()[0]
        clave = auth.create_api_key(conn, pk, "test-store")

    with TestClient(api_app) as api:
        def send(method, path, body, headers):
            r = api.request(method, path, json=body, headers=headers)
            return r.status_code, r.json()

        import importlib.util
        spec = importlib.util.spec_from_file_location("store_app", REPO_ROOT / "store/app.py")
        store = importlib.util.module_from_spec(spec)
        sys.modules["store_app"] = store
        spec.loader.exec_module(store)
        tienda = TestClient(store.crear_app(send=send, api_url=API, clave=clave,
                                            terminal="TID00010",
                                            public_url="http://localhost:8090"))
        yield tienda, api, clave, store


def _carrito(**kw):
    return {"lineas": [{"sku": "ARZ5", "cantidad": 2}, {"sku": "PAN6", "cantidad": 1}], **kw}


def test_el_catalogo_no_expone_la_clave(entorno):
    tienda, _, clave, _ = entorno
    r = tienda.get("/api/catalogo")
    assert r.status_code == 200 and r.json()["configurada"] is True
    assert clave not in r.text
    assert clave not in tienda.get("/").text


def test_pago_online_aprobado_con_el_total_del_servidor(entorno):
    tienda, _, _, _ = entorno
    r = tienda.post("/api/pagar-online", json=_carrito(payment_method="pm_card_visa"))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["estado"] == "pagado"
    # 2 × 24.90 + 3.00, con los precios del catalogo de la tienda.
    assert body["importe"] == 2 * 2490 + 300
    fila = _query(
        """SELECT pi.amount_minor, pi.channel::text, m.trade_name, pi.line_items
           FROM acquiring.payment_intents pi JOIN acquiring.merchants m ON m.id = pi.merchant_id
           WHERE pi.intent_reference = %s""", (body["pedido"],))[0]
    assert fila["amount_minor"] == 5280 and fila["channel"] == "CARD_NOT_PRESENT"
    assert fila["trade_name"] == "Mercado Andino" and len(fila["line_items"]) == 2


def test_el_navegador_no_puede_poner_el_precio(entorno):
    """Un carrito con precios manipulados: la tienda los ignora."""
    tienda, _, _, _ = entorno
    r = tienda.post("/api/pagar-online", json={
        "lineas": [{"sku": "CAF2", "cantidad": 1, "precio": 1}],
        "payment_method": "pm_card_visa"})
    assert r.json()["importe"] == 1890


def test_un_producto_inventado_se_rechaza(entorno):
    tienda, _, _, _ = entorno
    r = tienda.post("/api/pagar-online", json={"lineas": [{"sku": "TV65", "cantidad": 1}],
                                              "payment_method": "pm_card_visa"})
    assert r.status_code == 422


@pytest.mark.parametrize("pm, codigo", [("pm_card_visa_chargeDeclined", "05"),
                                        ("pm_card_visa_chargeDeclinedInsufficientFunds", "51"),
                                        ("pm_card_chargeDeclinedExpiredCard", "54")])
def test_cada_tarjeta_de_prueba_da_su_rechazo(entorno, pm, codigo):
    tienda, _, _, _ = entorno
    r = tienda.post("/api/pagar-online", json=_carrito(payment_method=pm))
    assert r.json()["estado"] == "rechazado" and r.json()["codigo"] == codigo
    assert r.json()["mensaje"]


def test_3ds_manda_al_banco_y_vuelve_a_la_tienda(entorno):
    tienda, api, _, _ = entorno
    r = tienda.post("/api/pagar-online", json=_carrito(payment_method="pm_card_threeDSecure2Required"))
    body = r.json()
    assert body["estado"] == "verificar"
    url = urlparse(body["redirigir"])
    assert url.path == f"/3ds/{body['pedido']}"

    # El comprador se autentica en la pagina del banco (la API de FinCorp)...
    token = parse_qs(url.query)["t"][0]
    res = api.post(f"/v1/intents/{body['pedido']}/authenticate",
                   json={"token": token, "authenticated": True}).json()
    # ...y vuelve a la tienda, que consulta el resultado al servidor.
    assert res["return_url"] == "http://localhost:8090/gracias"
    pedido = tienda.get(f"/api/pedidos/{body['pedido']}").json()
    assert pedido["estado"] == "SUCCEEDED"


def test_pagar_en_caja_abre_el_cobro_en_el_terminal_y_da_un_qr(entorno):
    tienda, api, _, _ = entorno
    r = tienda.post("/api/pagar-en-caja", json=_carrito())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url_pago"] == f"{API}/pay/{body['pedido']}"
    assert tienda.get(f"/api/pedidos/{body['pedido']}").json()["estado"] == "REQUIRES_CONFIRMATION"

    svg = tienda.get(f"/api/qr/{body['pedido']}.svg")
    assert svg.status_code == 200 and svg.text.lstrip().startswith("<?xml")

    # El comprador paga desde su movil en la pagina de FinCorp.
    assert api.post(f"/v1/intents/{body['pedido']}/confirm", json={"channel": "QR"}).status_code == 200
    assert tienda.get(f"/api/pedidos/{body['pedido']}").json()["estado"] == "SUCCEEDED"
    terminal = _query("""SELECT t.terminal_id FROM acquiring.payment_intents pi
                         JOIN acquiring.terminals t ON t.id = pi.terminal_id
                         WHERE pi.intent_reference = %s""", (body["pedido"],))
    assert terminal == [{"terminal_id": "TID00010"}]


def test_una_referencia_rara_no_llega_a_la_api(entorno):
    tienda, _, _, _ = entorno
    assert tienda.get("/api/pedidos/..%2F..%2Fadmin").status_code in (404, 422)
    assert tienda.get("/api/qr/a-b.svg").status_code == 422


def test_sin_configurar_la_tienda_lo_dice(entorno):
    _, _, _, store = entorno
    from fastapi.testclient import TestClient

    vacia = TestClient(store.crear_app(send=lambda *a: (0, {}), api_url="", clave=""))
    assert vacia.get("/api/catalogo").json()["configurada"] is False


def test_el_javascript_de_la_tienda_es_valido():
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node no esta instalado")
    for pagina in ("index.html", "gracias.html"):
        html = (REPO_ROOT / "store/web" / pagina).read_text(encoding="utf-8")
        for js in re.findall(r"<script>(.*?)</script>", html, re.DOTALL):
            proc = subprocess.run([node, "--check", "-"], input=js, capture_output=True, text=True)
            assert proc.returncode == 0, (pagina, proc.stderr)


def test_la_tienda_nunca_acepta_un_numero_de_tarjeta(entorno):
    """
    El backend de la tienda solo admite tokens de prueba. Un numero de tarjeta
    —aunque sea el de prueba— no pasa la validacion: al servidor solo puede
    llegar un token, que es lo que la saca del alcance de PCI.
    """
    tienda, _, _, _ = entorno
    for intento in ("4242424242424242", "pm_card_inventada", "pm_card_visa; DROP"):
        r = tienda.post("/api/pagar-online", json=_carrito(payment_method=intento))
        assert r.status_code == 422, intento


@pytest.mark.parametrize("pm", ["pm_card_mastercard", "pm_card_amex"])
def test_otras_marcas_se_aprueban_en_la_tienda(entorno, pm):
    tienda, _, _, _ = entorno
    assert tienda.post("/api/pagar-online", json=_carrito(payment_method=pm)).json()["estado"] == "pagado"
