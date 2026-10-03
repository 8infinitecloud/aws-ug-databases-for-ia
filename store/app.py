"""
Mercado Andino — la tienda online de la demo.

ESTO ES EL COMERCIO, NO EL ADQUIRENTE. Es un sistema distinto de FinCorp, como
lo es cualquier tienda que cobra con un procesador de pagos: tiene su catálogo,
sus precios y su propio backend, y le habla a la API de FinCorp con SU clave de
API, igual que una tienda le habla a Stripe.

Por eso existe este backend y no basta con una página: la clave secreta del
comercio (`sk_demo_…`) no puede llegar nunca al navegador. Si estuviera en el
JavaScript, cualquiera con las herramientas del navegador podría copiarla y
cobrar en nombre de la tienda.

Y por eso el total lo calcula AQUÍ, con los precios del catálogo del servidor:
el navegador solo dice qué productos y cuántos. Un carrito que llegara con
precios desde el cliente se podría manipular para pagar un sol por un televisor.

Dos formas de pagar, que son los dos canales que la demo compara:

  online   el comprador elige una tarjeta de prueba → CARD_NOT_PRESENT, con
           3-D Secure si el "banco" la pide
  en caja  la tienda abre el cobro en su terminal y muestra un QR grande; se paga
           desde el móvil en la página de FinCorp → QR

DEMO: no hay tarjetas reales. Las "tarjetas" son los PaymentMethods de prueba de
la API de FinCorp (pm_card_visa...), con los mismos identificadores que Stripe.

Configuración, por entorno (la rellena scripts/store_local.sh desde Terraform):
    FINCORP_API_URL     la API del adquirente (terraform output api_endpoint)
    STORE_API_KEY       la clave del comercio (.demo_keys.json, MID000000020012)
    STORE_TERMINAL_ID   el terminal de la caja (TID00010)
    STORE_PUBLIC_URL    dónde se abre la tienda, para la vuelta del 3DS
"""

from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel, Field

WEB = Path(__file__).resolve().parent / "web"

# (método, ruta, cuerpo, cabeceras) -> (status, json). Así las pruebas conectan
# la tienda con la API de FinCorp en memoria, sin red.
Send = Callable[[str, str, dict | None, dict], tuple[int, dict]]


# ── El catálogo es de la tienda, no del adquirente ───────────────────────────
# Los precios viven aquí, en el servidor. Productos genéricos de supermercado:
# la tienda es ficticia y no vende ninguna marca real.
CATALOGO = {
    "ARZ5": {"nombre": "Arroz extra 5 kg", "precio": 2490, "emoji": "🍚"},
    "LCH6": {"nombre": "Leche evaporada x6", "precio": 2160, "emoji": "🥛"},
    "PAL1": {"nombre": "Palta fuerte 1 kg", "precio": 1290, "emoji": "🥑"},
    "PAN6": {"nombre": "Pan francés x6", "precio": 300, "emoji": "🥖"},
    "POL1": {"nombre": "Pollo entero 1 kg", "precio": 1190, "emoji": "🍗"},
    "CAF2": {"nombre": "Café tostado 250 g", "precio": 1890, "emoji": "☕"},
    "QUI1": {"nombre": "Quinua 1 kg", "precio": 1450, "emoji": "🌾"},
    "GAS3": {"nombre": "Gaseosa 3 L", "precio": 1090, "emoji": "🥤"},
}

# Las tarjetas de prueba que ve el comprador, y el PaymentMethod que hay detrás.
TARJETAS = {
    "visa": ("Visa terminada en 4242", "pm_card_visa"),
    "3ds": ("Visa que pide verificación del banco (3220)", "pm_card_threeDSecure2Required"),
    "rechazada": ("Visa rechazada por el banco (0002)", "pm_card_visa_chargeDeclined"),
    "sin_fondos": ("Visa sin fondos (9995)", "pm_card_visa_chargeDeclinedInsufficientFunds"),
    "vencida": ("Visa vencida (0069)", "pm_card_chargeDeclinedExpiredCard"),
}

MENSAJES = {
    "05": "El banco no autorizó el pago. Prueba con otra tarjeta.",
    "51": "La tarjeta no tiene fondos suficientes.",
    "54": "La tarjeta está vencida.",
}


class Linea(BaseModel):
    sku: str = Field(max_length=8)
    cantidad: int = Field(gt=0, le=20)


class Carrito(BaseModel):
    lineas: list[Linea] = Field(min_length=1, max_length=20)


class PagoOnline(Carrito):
    tarjeta: Literal["visa", "3ds", "rechazada", "sin_fondos", "vencida"]


def _total(carrito: Carrito) -> tuple[int, list[dict]]:
    """El importe y las líneas, con los precios DEL SERVIDOR."""
    lineas = []
    for linea in carrito.lineas:
        producto = CATALOGO.get(linea.sku)
        if producto is None:
            raise HTTPException(422, f"producto {linea.sku} no existe")
        lineas.append({
            "sku": linea.sku, "name": producto["nombre"], "quantity": linea.cantidad,
            "unit_price_minor": producto["precio"],
        })
    return sum(item["quantity"] * item["unit_price_minor"] for item in lineas), lineas


def http_sender(base_url: str, timeout: float = 15.0) -> Send:
    base = base_url.rstrip("/")

    def send(method: str, path: str, body: dict | None, headers: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(base + path, data=data, method=method,
                                     headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read() or b"{}")
            except json.JSONDecodeError:
                return err.code, {}
        except (urllib.error.URLError, TimeoutError):
            return 0, {"error": "la API de pagos no responde"}

    return send


def crear_app(send: Send | None = None, api_url: str | None = None, clave: str | None = None,
              terminal: str | None = None, public_url: str | None = None) -> FastAPI:
    api_url = (api_url or os.getenv("FINCORP_API_URL", "")).rstrip("/")
    clave = clave or os.getenv("STORE_API_KEY", "")
    terminal = terminal or os.getenv("STORE_TERMINAL_ID", "TID00010")
    public_url = (public_url or os.getenv("STORE_PUBLIC_URL", "http://localhost:8090")).rstrip("/")
    send = send or http_sender(api_url)
    autorizacion = {"Authorization": f"Bearer {clave}"}

    app = FastAPI(title="Mercado Andino (tienda de demo)", docs_url=None, redoc_url=None)

    def _ref(prefijo: str) -> str:
        # Corta: viaja en el QR, y cada caracter de más lo hace más denso.
        return f"{prefijo}{secrets.token_hex(5).upper()}"

    def _fallo(status: int, body: dict):
        detalle = body.get("error") or "error de la pasarela de pagos"
        raise HTTPException(502 if status in (0,) or status >= 500 else status, detalle)

    @app.get("/", response_class=HTMLResponse)
    def inicio():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/gracias", response_class=HTMLResponse)
    def gracias():
        return FileResponse(WEB / "gracias.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/catalogo")
    def catalogo():
        return {
            "productos": [{"sku": k, **v} for k, v in CATALOGO.items()],
            "tarjetas": [{"id": k, "nombre": v[0]} for k, v in TARJETAS.items()],
            "configurada": bool(api_url and clave),
        }

    @app.post("/api/pagar-online")
    def pagar_online(pedido: PagoOnline):
        """
        Lo que haría el backend de cualquier tienda con Stripe: crear la
        intención con su clave, confirmarla con el medio de pago, y si el banco
        pide autenticar, devolver al navegador la URL a la que mandar al
        comprador.
        """
        importe, lineas = _total(pedido)
        ref = _ref("PIMA")
        s, body = send("POST", "/v1/intents", {
            "intent_reference": ref, "amount_minor": importe, "currency_code": "PEN",
            "line_items": lineas, "statement_description": "MERCADO ANDINO",
            "return_url": f"{public_url}/gracias",
        }, {**autorizacion, "Idempotency-Key": f"tienda-c-{ref}"})
        if s != 201:
            _fallo(s, body)

        s, cobro = send("POST", f"/v1/intents/{ref}/confirm", {
            "channel": "CARD_NOT_PRESENT", "payment_method": TARJETAS[pedido.tarjeta][1],
        }, {**autorizacion, "Idempotency-Key": f"tienda-p-{ref}"})
        if s != 200:
            _fallo(s, cobro)

        if cobro.get("status") == "REQUIRES_ACTION":
            return {"pedido": ref, "estado": "verificar",
                    "redirigir": cobro["next_action"]["redirect_to_url"]["url"]}
        if cobro.get("status") == "CAPTURED":
            return {"pedido": ref, "estado": "pagado", "importe": importe,
                    "codigo_autorizacion": cobro.get("auth_code"), "rrn": cobro.get("rrn")}
        codigo = cobro.get("response_code")
        return {"pedido": ref, "estado": "rechazado", "codigo": codigo,
                "mensaje": MENSAJES.get(codigo, "El pago no se pudo completar.")}

    @app.post("/api/pagar-en-caja")
    def pagar_en_caja(carrito: Carrito):
        """
        La tienda abre el cobro en su terminal y muestra el QR.

        Es el mismo flujo que el Core2, pero con el QR en el proyector en vez
        de en una pantalla de 2 pulgadas. El comprador paga en la página de
        FinCorp; la tienda solo espera.
        """
        importe, lineas = _total(carrito)
        ref = _ref("PIMC")
        s, body = send("POST", "/v1/intents", {
            "intent_reference": ref, "terminal_id": terminal, "amount_minor": importe,
            "currency_code": "PEN", "line_items": lineas,
        }, {**autorizacion, "Idempotency-Key": f"tienda-q-{ref}"})
        if s != 201:
            _fallo(s, body)
        return {"pedido": ref, "importe": importe, "url_pago": f"{api_url}/pay/{ref}"}

    @app.get("/api/pedidos/{ref}")
    def pedido(ref: str):
        """El estado del pedido, que la página consulta mientras espera."""
        if not ref.isalnum() or len(ref) > 40:
            raise HTTPException(422, "referencia invalida")
        s, intent = send("GET", f"/v1/intents/{ref}", None, autorizacion)
        if s != 200:
            _fallo(s, intent)
        return {"pedido": ref, "estado": intent["status"], "importe": intent["amount_minor"],
                "ultimo_error": intent.get("last_error_code")}

    @app.get("/api/qr/{ref}.svg")
    def qr(ref: str):
        """
        El QR, generado aquí en SVG. Sin librerías de JavaScript de un CDN: si la
        WiFi del evento no las cargara, el QR no saldría en el peor momento.
        """
        import io

        import segno

        if not ref.isalnum() or len(ref) > 40:
            raise HTTPException(422, "referencia invalida")
        buf = io.BytesIO()
        segno.make(f"{api_url}/pay/{ref}", error="m").save(buf, kind="svg", scale=10, border=2)
        return Response(buf.getvalue(), media_type="image/svg+xml",
                        headers={"Cache-Control": "no-store"})

    return app


app = crear_app()
