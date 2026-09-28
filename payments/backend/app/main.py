"""
API de adquirencia — el servicio que recibe los pagos.

DEMO EDUCATIVA. No procesa pagos reales: no hay tarjetas reales, ni CVV reales,
ni credenciales bancarias, ni conexion a ninguna red de pagos. La autorizacion
esta simulada en `acquiring.authorize_mock`, y es lo unico falso del sistema.
Todo lo demas —el ledger, el MDR, la idempotencia, los data elements de ISO
8583— se comporta como en un adquirente de verdad.

Tres rutas, que son las tres cosas que pasan en un cobro:

    POST /v1/intents                      el POS cierra el carrito
    GET  /v1/intents/{ref}                la web del QR lee que se va a cobrar
    POST /v1/intents/{ref}/confirm        se autoriza, captura y asienta

Ejecutar en local:
    uvicorn app.main:app --reload --port 8080
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, Union

from fastapi import Body, FastAPI, Header, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from psycopg_pool import ConnectionPool

from . import acquiring, idempotency
from .config import get_settings
from .events import EventPublisher, build_payment_event
from .models import (
    ChargeResponse,
    ConfirmCardRequest,
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
# una con QR no lleva credencial ninguna.
ConfirmRequest = Annotated[
    Union[ConfirmCardRequest, ConfirmQrRequest],
    Field(discriminator="channel"),
]

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
):
    """
    Crea la intencion de cobro. La llama el POS al cerrar el carrito.

    El POS genera `intent_reference` porque el QR tiene que existir en pantalla
    antes de que haya una respuesta de la base.
    """
    with pool.connection() as conn:
        merchant = acquiring.get_merchant(conn, settings.default_merchant_id)
        merchant_pk = merchant["id"]

    payload = body.model_dump(mode="json")

    def operacion():
        with pool.connection() as conn:
            intent = acquiring.create_intent(
                conn,
                merchant_id=settings.default_merchant_id,
                terminal_id=body.terminal_id,
                intent_reference=body.intent_reference,
                amount_minor=body.amount_minor,
                currency_code=body.currency_code,
                line_items=[item.model_dump() for item in body.line_items],
                statement_description=body.statement_description,
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
    intent_reference: str,
    body: Annotated[ConfirmRequest, Body()],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    """
    Autoriza, captura y asienta en el libro mayor.

    Una declinacion **no** es un error HTTP: devuelve 200 con
    `response_code: "05"`. El POS necesita el codigo para decidir que muestra en
    pantalla, y un 4xx le quitaria el dato.
    """
    card_token = getattr(body, "card_token", None)
    cvv = getattr(body, "cvv", None)  # nunca se registra en el log

    with pool.connection() as conn:
        intent = acquiring.get_intent(conn, intent_reference)
        merchant_pk = intent["merchant_id"]

    # El CVV queda fuera de la huella: el cuerpo que identifica la operacion es
    # el canal y la tarjeta, no la credencial.
    payload = {"channel": body.channel, "card_token": card_token}

    def operacion():
        with pool.connection() as conn:
            charge = acquiring.confirm_intent(
                conn,
                intent_reference=intent_reference,
                channel=body.channel,
                cvv=cvv,
                expected_cvv=settings.demo_cvv,
                card_token=card_token,
            )
            terminal = acquiring.get_terminal_context(conn, intent.get("terminal_id"))
        # Fuera del `with`: el evento se publica cuando el cobro ya esta
        # confirmado en la base. Publicar dentro anunciaria un cobro que todavia
        # podria deshacerse.
        _publish(charge, intent, terminal)
        return 200, ChargeResponse(**charge).model_dump(mode="json")

    status_code, saved = _with_idempotency(
        merchant_pk, idempotency_key, "POST",
        f"/v1/intents/{intent_reference}/confirm", payload,
        operacion, resource_type="charge",
    )
    return JSONResponse(saved, status_code)


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
