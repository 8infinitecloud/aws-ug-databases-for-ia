"""
API de adquirencia — el servicio que recibe los pagos.

DEMO EDUCATIVA. No procesa pagos reales: no hay tarjetas reales, ni CVV reales,
ni credenciales bancarias, ni conexion a ninguna red de pagos. La autorizacion
esta simulada en `acquiring.authorize_mock`, y es lo unico falso del sistema.
Todo lo demas —el ledger, el MDR, la idempotencia, los data elements de ISO
8583— se comporta como en un adquirente de verdad.

Las rutas siguen el ciclo de vida de un PaymentIntent de Stripe:

    POST /v1/intents                      el POS cierra el carrito, o una tienda
                                          online abre el pago (con su clave)
    GET  /v1/intents/{ref}                la web del QR lee que se va a cobrar
    POST /v1/intents/{ref}/confirm        se autoriza, captura y asienta
    POST /v1/intents/{ref}/cancel         se abandona sin cobrar
    GET  /3ds/{ref}                       el banco simulado autentica (online)
    POST /v1/intents/{ref}/authenticate   resultado de esa autenticacion

Dos clientes y dos formas de identificarse, ver auth.py: el POS por su
terminal, la tienda online por su clave de API.

Ejecutar en local:
    uvicorn app.main:app --reload --port 8080
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Union

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import Field
from psycopg_pool import ConnectionPool

from . import acquiring, auth, idempotency
from .config import get_settings
from .events import EventPublisher, build_payment_event
from .models import (
    AuthenticationResult,
    CancelRequest,
    ChargeResponse,
    ConfirmCardRequest,
    ConfirmOnlineRequest,
    ConfirmQrRequest,
    CreateIntentRequest,
    HealthResponse,
    IntentResponse,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("acquiring.api")

# El canal discrimina el cuerpo: una confirmacion con tarjeta lleva token y CVV,
# una con QR no lleva credencial ninguna, y una online lleva un PaymentMethod.
ConfirmRequest = Annotated[
    Union[ConfirmCardRequest, ConfirmQrRequest, ConfirmOnlineRequest],
    Field(discriminator="channel"),
]

# Los canales de cada tipo de intencion. Una intencion de POS no se paga online
# ni al reves: mezclarlos permitiria cobrar una venta de mostrador con una
# tarjeta que nunca estuvo en el mostrador.
CANALES_POS = ("CARD_PRESENT", "QR")
CANALES_ONLINE = ("CARD_NOT_PRESENT",)

def _localizar_web() -> Path | None:
    """
    Encuentra la carpeta `web/`, que esta en un sitio distinto segun donde corra.

    En la imagen el codigo vive en /srv/app y la pagina en /srv/web; en el repo
    son payments/backend/app y payments/web. Probar los dos sitios evita tener
    que configurar una variable para algo que siempre esta en uno de ellos.
    """
    import os

    if os.getenv("WEB_DIR"):
        candidatos = [Path(os.environ["WEB_DIR"])]
    else:
        aqui = Path(__file__).resolve().parent
        candidatos = [aqui.parent / "web", aqui.parent.parent / "web"]

    for ruta in candidatos:
        if (ruta / "index.html").is_file():
            return ruta
    return None


WEB_DIR = _localizar_web()

settings = get_settings()
publisher = EventPublisher(
    endpoint=settings.iot_endpoint,
    region=settings.aws_region,
    topic_prefix=settings.iot_topic_prefix,
)
pool: ConnectionPool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Abre el pool sin esperar a que la base responda.

    `wait=False` es deliberado: si Aurora esta arrancando (Serverless v2 escala
    desde cero), un pool que bloquea impide que el contenedor arranque, el
    health check del ALB nunca pasa y ECS lo mata en bucle sin dejar rastro de
    la causa. Asi el contenedor arranca, /health dice la verdad, y el log
    explica que falta.
    """
    global pool
    pool = ConnectionPool(
        settings.db_conninfo, min_size=1, max_size=4, timeout=10, open=False
    )
    pool.open(wait=False)
    log.info(
        "servicio arriba | db=%s:%s/%s | iot=%s",
        settings.db_host,
        settings.db_port,
        settings.db_name,
        settings.iot_endpoint or "sin configurar (los eventos van al log)",
    )
    if WEB_DIR is None:
        log.warning("no se encontro web/index.html: /pay/{ref} respondera 503")
    yield
    pool.close()


app = FastAPI(
    title="FinCorp Acquiring API",
    description=(
        "API de adquirencia para el taller *De los Datos a la IA*. "
        "**No procesa pagos reales**: la autorizacion esta simulada."
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# ── Traduccion de errores del dominio ────────────────────────────────────────
@app.exception_handler(acquiring.AcquiringError)
async def handle_acquiring_error(request: Request, exc: acquiring.AcquiringError):
    log.warning("%s %s -> %s: %s", request.method, request.url.path, exc.status_code, exc)
    return JSONResponse({"error": str(exc), "type": "acquiring_error"}, exc.status_code)


@app.exception_handler(idempotency.IdempotencyConflict)
async def handle_idempotency_conflict(
    request: Request, exc: idempotency.IdempotencyConflict
):
    log.warning("conflicto de idempotencia en %s: %s", request.url.path, exc)
    return JSONResponse({"error": str(exc), "type": "idempotency_conflict"}, exc.status_code)


# ── Salud ────────────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthResponse, tags=["operacion"])
def health():
    """
    Lo que consultan el health check del ALB y `validate_setup.py`.

    Devuelve 200 aunque la base no responda, con `database: "unreachable"`. Un
    503 aqui haria que ECS reciclara la tarea en bucle por un problema que no
    esta en la tarea, y el log del contenedor se perderia con ella.
    """
    database = "unreachable"
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
        database = "ok"
    except Exception as exc:  # noqa: BLE001 — el estado se reporta, no se propaga
        log.error("health: la base no responde: %s", exc)

    return HealthResponse(
        status="ok" if database == "ok" else "degraded",
        database=database,
        iot="ok" if publisher.enabled else "not_configured",
    )


# ── Intenciones de cobro ─────────────────────────────────────────────────────
@app.post(
    "/v1/intents",
    response_model=IntentResponse,
    status_code=201,
    tags=["cobros"],
)
def create_intent(
    body: CreateIntentRequest,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """
    Crea la intencion de cobro.

    El POS la pide con su `terminal_id` al cerrar el carrito, y genera
    `intent_reference` porque el QR tiene que existir en pantalla antes de que
    haya una respuesta de la base.

    Una tienda online la pide sin terminal y con `Authorization: Bearer <clave>`.
    """
    merchant_code = _merchant_from_key(authorization)
    with pool.connection() as conn:
        terminal = (
            acquiring.get_terminal(conn, body.terminal_id) if body.terminal_id else None
        )
        if merchant_code is None and terminal is None:
            raise acquiring.AcquiringError(
                "falta el terminal_id (POS) o la clave de API del comercio (online)", 401
            )
        if terminal and merchant_code and terminal["merchant_code"] != merchant_code:
            # El terminal es de otro comercio: no se dice de cual.
            raise acquiring.AcquiringError(f"terminal {body.terminal_id} no existe", 404)
        merchant_code = merchant_code or terminal["merchant_code"]
        merchant_pk = acquiring.get_merchant(conn, merchant_code)["id"]

    payload = body.model_dump(mode="json")

    def operacion():
        with pool.connection() as conn:
            intent = acquiring.create_intent(
                conn,
                merchant_id=merchant_code,
                terminal=terminal,
                intent_reference=body.intent_reference,
                amount_minor=body.amount_minor,
                currency_code=body.currency_code,
                line_items=[item.model_dump() for item in body.line_items],
                statement_description=body.statement_description,
                return_url=body.return_url,
            )
        return 201, _intent_response(intent).model_dump(mode="json")

    status_code, saved = _with_idempotency(
        merchant_pk, idempotency_key, "POST", "/v1/intents", payload,
        operacion, resource_type="payment_intent",
        resource_id=body.intent_reference,
    )
    return JSONResponse(saved, status_code)


@app.get(
    "/v1/intents/{intent_reference}",
    response_model=IntentResponse,
    tags=["cobros"],
)
def read_intent(intent_reference: str):
    """La lee la web del QR para mostrar importe y comercio antes de confirmar."""
    with pool.connection() as conn:
        return _intent_response(acquiring.get_intent(conn, intent_reference))


@app.post(
    "/v1/intents/{intent_reference}/confirm",
    response_model=ChargeResponse,
    tags=["cobros"],
)
def confirm_intent(
    request: Request,
    intent_reference: str,
    body: Annotated[ConfirmRequest, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    authorization: Annotated[str | None, Header()] = None,
):
    """
    Autoriza, captura y asienta en el libro mayor.

    Una declinacion **no** es un error HTTP: devuelve 200 con
    `response_code: "05"`. El POS necesita el codigo para decidir que muestra en
    pantalla, y un 4xx le quitaria el dato. Tras una declinacion la intencion
    vuelve a `REQUIRES_PAYMENT_METHOD` y admite otro intento.

    Online, si el emisor pide autenticar, devuelve `status: REQUIRES_ACTION` y un
    `next_action` con la URL a la que hay que mandar al comprador.
    """
    card_token = getattr(body, "card_token", None)
    cvv = getattr(body, "cvv", None)  # nunca se registra en el log
    payment_method = getattr(body, "payment_method", None)

    with pool.connection() as conn:
        intent = acquiring.get_intent(conn, intent_reference)
    _check_access(intent, authorization)
    _check_channel(intent, body.channel)
    merchant_pk = intent["merchant_id"]

    # El CVV queda fuera de la huella: el cuerpo que identifica la operacion es
    # el canal y la tarjeta, no la credencial.
    payload = {"channel": body.channel, "card_token": card_token,
               "payment_method": payment_method}

    def operacion():
        with pool.connection() as conn:
            charge = acquiring.confirm_intent(
                conn,
                intent_reference=intent_reference,
                channel=body.channel,
                cvv=cvv,
                expected_cvv=settings.demo_cvv,
                card_token=card_token,
                payment_method=payment_method,
                authentication_url=_public_url(request, f"/3ds/{intent_reference}"),
            )
            terminal = acquiring.get_terminal_context(conn, intent.get("terminal_id"))
        # Fuera del `with`: el evento se publica cuando el cobro ya esta
        # confirmado en la base. Publicar dentro anunciaria un cobro que todavia
        # podria deshacerse.
        #
        # Y solo si el cobro es NUEVO. El POS reconfirma al detectar SUCCEEDED
        # para leer los datos del comprobante, y sin esta guarda cada pago por QR
        # publicaba DOS eventos identicos. El scoring del Lab 4 los contaba como
        # dos operaciones: los velocity checks saltaban a la mitad del umbral
        # real. Visto en el dispositivo: un cobro daba "2 tx / 61.00" en la
        # ventana.
        #
        # Tampoco si todavia no hay cobro (REQUIRES_ACTION): no hay operacion
        # que puntuar hasta que el comprador se autentique.
        if charge.get("already_captured"):
            log.info("cobro %s ya existia; no se republica el evento",
                     charge.get("charge_reference"))
        elif charge.get("charge_reference"):
            _publish(charge, intent, terminal)
        return 200, ChargeResponse(**charge).model_dump(mode="json")

    status_code, saved = _with_idempotency(
        merchant_pk, idempotency_key, "POST",
        f"/v1/intents/{intent_reference}/confirm", payload,
        operacion, resource_type="charge",
    )
    return JSONResponse(saved, status_code)


@app.post(
    "/v1/intents/{intent_reference}/cancel",
    response_model=IntentResponse,
    tags=["cobros"],
)
def cancel_intent(
    intent_reference: str,
    body: Annotated[CancelRequest, Body()] = CancelRequest(),
    authorization: Annotated[str | None, Header()] = None,
):
    """
    Cancela una intencion sin cobrar. Cancelar dos veces devuelve la misma.

    Un pago abandonado es un dato: es el que separa los carritos abiertos de los
    cobrados en el embudo de la tienda.
    """
    with pool.connection() as conn:
        intent = acquiring.get_intent(conn, intent_reference)
    _check_access(intent, authorization)
    with pool.connection() as conn:
        return _intent_response(
            acquiring.cancel_intent(conn, intent_reference, body.cancellation_reason)
        )


# ── Autenticacion 3DS simulada ───────────────────────────────────────────────
@app.get("/3ds/{intent_reference}", response_class=HTMLResponse, tags=["online"])
def pagina_3ds(intent_reference: str):
    """
    El "banco emisor" que autentica al comprador. Simulado.

    En un pago real esta pagina es del banco del comprador, no del adquirente.
    Aqui la sirve la API para que el flujo online sea completo sin depender de
    nadie. Igual que la del QR, no lleva nada inyectado: lee la referencia y el
    token de su propia URL.
    """
    if WEB_DIR is None or not (WEB_DIR / "3ds.html").is_file():
        raise acquiring.AcquiringError("la pagina 3DS no esta desplegada", 503)
    return HTMLResponse(
        (WEB_DIR / "3ds.html").read_text(encoding="utf-8"),
        headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


@app.post(
    "/v1/intents/{intent_reference}/authenticate",
    response_model=ChargeResponse,
    tags=["online"],
)
def authenticate_intent(intent_reference: str, body: AuthenticationResult):
    """
    El resultado de la autenticacion, que manda la pagina del banco simulado.

    No lleva clave de API: llega desde el navegador del comprador. Lo protege el
    token de un solo uso que viaja en la URL de `next_action`.
    """
    with pool.connection() as conn:
        intent = acquiring.get_intent(conn, intent_reference)
        result = acquiring.complete_authentication(
            conn, intent_reference, body.token, body.authenticated
        )
    if result.get("charge_reference"):
        _publish(result, intent, {})
    return JSONResponse(ChargeResponse(**result).model_dump(mode="json"))


# ── Quien puede tocar una intencion ──────────────────────────────────────────
def _merchant_from_key(authorization: str | None) -> str | None:
    """El MID del comercio duenio de la clave, o None si no vino clave."""
    key = auth.parse_bearer(authorization)
    if key is None:
        return None
    with pool.connection() as conn:
        return auth.authenticate(conn, key, auth.SCOPE_WRITE_INTENTS)["merchant_id"]


def _check_access(intent: dict, authorization: str | None) -> None:
    """
    Una intencion online solo la toca su comercio, con su clave.

    Las del POS no exigen clave, igual que antes: las confirma el terminal o el
    movil que escaneo el QR. Si viene una clave, tiene que ser la del duenio, y
    si no lo es se responde 404: confirmar que la referencia existe ya seria
    revelar algo de otro comercio.
    """
    merchant_code = _merchant_from_key(authorization)
    if intent["terminal_id"] is None and merchant_code is None:
        raise acquiring.AcquiringError("esta intencion exige la clave de API del comercio", 401)
    if merchant_code is not None and merchant_code != intent["merchant_code"]:
        raise acquiring.AcquiringError(
            f"intencion {intent['intent_reference']} no existe", 404
        )


def _check_channel(intent: dict, channel: str) -> None:
    permitidos = CANALES_ONLINE if intent["terminal_id"] is None else CANALES_POS
    if channel not in permitidos:
        raise acquiring.AcquiringError(
            f"el canal {channel} no aplica a esta intencion (admite {', '.join(permitidos)})",
            422,
        )


def _public_url(request: Request, path: str) -> str:
    """
    La URL publica de una ruta de esta API.

    Detras de API Gateway el host que ve el contenedor es el del balanceador, no
    el que conoce el comprador. PUBLIC_BASE_URL lo fija; en local basta el de la
    peticion.
    """
    base = settings.public_base_url or str(request.base_url)
    return base.rstrip("/") + path


# ── La pagina de pago del QR ─────────────────────────────────────────────────
@app.get("/pay/{intent_reference}", response_class=HTMLResponse, tags=["cobros"])
def pagina_de_pago(intent_reference: str):
    """
    La pagina que se abre al escanear el QR del POS.

    Se sirve desde la propia API y no desde un bucket en S3 a proposito. El QR
    lleva una URL, el movil la abre, y esa pagina llama a la API: si vive en
    otro origen hacen falta CORS abiertos —la configuracion anterior tenia
    `allow_origins = ["*"]` en una API de pagos— y un bucket publico servido por
    HTTP. Con el mismo origen no hace falta ninguna de las dos cosas, y el QR
    lleva una sola URL en vez de dos.

    La referencia no se inyecta en el HTML: la pagina la lee de su propia URL.
    Asi el fichero es estatico de verdad y no hay plantilla que escapar.
    """
    if WEB_DIR is None:
        raise acquiring.AcquiringError("la pagina de pago no esta desplegada", 503)
    return HTMLResponse(
        (WEB_DIR / "index.html").read_text(encoding="utf-8"),
        headers={
            # El cobro cambia de estado al pagarlo; una pagina cacheada
            # mostraria un importe pendiente que ya no lo esta.
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        },
    )


# ── Ayudas ───────────────────────────────────────────────────────────────────
def _intent_response(intent: dict) -> IntentResponse:
    return IntentResponse(
        intent_reference=intent["intent_reference"],
        status=intent["status"],
        amount_minor=intent["amount_minor"],
        amount_received_minor=intent["amount_received_minor"],
        currency_code=intent["currency_code"],
        merchant_name=intent.get("trade_name") or "",
        line_items=intent.get("line_items") or [],
        created_at=intent["created_at"],
        channel=intent.get("channel"),
        last_error_code=intent.get("last_error_code"),
    )


def _publish(charge: dict, intent: dict, terminal: dict) -> None:
    event = build_payment_event(
        event_type="payment.captured" if charge["status"] == "CAPTURED" else "payment.declined",
        intent_reference=intent["intent_reference"],
        charge=charge,
        merchant_code=intent["merchant_code"],
        terminal_code=terminal.get("terminal_code"),
        device_serial=terminal.get("device_serial"),
        line_items=intent.get("line_items") or [],
        mcc=intent.get("mcc"),
        risk_level=intent.get("risk_level"),
    )
    publisher.publish(event, device_serial=terminal.get("device_serial"))


def _with_idempotency(
    merchant_pk: int | None,
    key: str | None,
    method: str,
    path: str,
    payload: dict,
    operacion,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> tuple[int, dict]:
    """
    Envuelve la operacion en la clave de idempotencia.

    Son tres transacciones separadas, y tiene que ser asi: la reserva se commitea
    **antes** de ejecutar para que una peticion concurrente con la misma clave la
    vea y reciba un 409 en vez de cobrar dos veces. Si la reserva viviera en la
    misma transaccion que el cobro, nadie mas la veria y el candado no serviria.

    Sin cabecera se ejecuta sin proteccion. La base sigue defendiendo con la
    unicidad de `intent_reference` y con la comprobacion de intencion ya cobrada,
    pero el POS deberia mandarla siempre.
    """
    if key is None:
        log.warning("%s %s sin Idempotency-Key", method, path)
        return operacion()

    # 1) Reservar y publicar la reserva.
    with pool.connection() as conn:
        guardada = idempotency.begin(conn, merchant_pk, key, method, path, payload)
    if guardada is not None:
        log.info("clave %s ya ejecutada; se devuelve la respuesta guardada", key)
        return guardada["status_code"], guardada["body"]

    # 2) Ejecutar y guardar el resultado.
    try:
        status_code, body = operacion()
    except Exception:
        # 3) Liberar, para que el reintento legitimo no choque con un candado
        # huerfano. Va en su propia transaccion porque la del paso 2 ya murio.
        with pool.connection() as conn:
            idempotency.release(conn, merchant_pk, key)
        raise

    with pool.connection() as conn:
        idempotency.complete(
            conn, merchant_pk, key, status_code, body,
            resource_type=resource_type,
            resource_id=resource_id or body.get("charge_reference"),
        )
    return status_code, body
