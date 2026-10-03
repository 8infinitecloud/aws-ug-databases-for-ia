"""
Dominio de adquirencia: autorizar, capturar y asentar en el libro mayor.

Todo lo que toca dinero pasa por aqui. Dos reglas que se cumplen sin excepcion:

  1. Una autorizacion, su captura y su asiento en el libro ocurren en UNA
     transaccion de base de datos. Si algo falla, no queda un cobro sin asiento.
  2. La comision se calcula desde el MDR del comercio leido en esa misma
     transaccion, no desde un valor cacheado: si a un comercio le cambian la
     tarifa, la siguiente operacion ya la usa.

DEMO: la autorizacion es un mock. No hay conexion con ninguna red de tarjetas.
"""

from __future__ import annotations

import logging
import random
import secrets
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone

from psycopg import Connection
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

# ── Response codes ISO 8583 (DE 39) ──────────────────────────────────────────
RC_APPROVED = "00"
RC_DO_NOT_HONOR = "05"
RC_INVALID_TRANSACTION = "12"
RC_INSUFFICIENT_FUNDS = "51"
RC_EXPIRED_CARD = "54"
RC_ISSUER_UNAVAILABLE = "91"

DECLINE_REASONS = {
    RC_DO_NOT_HONOR: "DO_NOT_HONOR",
    RC_INVALID_TRANSACTION: "INVALID_TRANSACTION",
    RC_INSUFFICIENT_FUNDS: "INSUFFICIENT_FUNDS",
    RC_EXPIRED_CARD: "EXPIRED_CARD",
    RC_ISSUER_UNAVAILABLE: "ISSUER_UNAVAILABLE",
}


class AcquiringError(Exception):
    """Error de negocio. El handler lo traduce a un 4xx."""

    def __init__(self, message: str, status_code: int = 400):
        super().__init__(message)
        self.status_code = status_code


@dataclass
class AuthorizationResult:
    approved: bool
    response_code: str
    auth_code: str | None
    rrn: str | None
    stan: str | None
    decline_reason: str | None


# ── El mock de autorizacion ──────────────────────────────────────────────────
# El canal online decide por TOKEN, como los PaymentMethods de prueba de Stripe
# (https://docs.stripe.com/testing). Cada token fuerza un camino del emisor, y
# asi la demo y el generador de trafico pueden ensenar cada uno a voluntad, sin
# un solo numero de tarjeta. Los tokens existen en payment_methods (002_seed).
TEST_PAYMENT_METHODS: dict[str, str] = {
    "pm_card_visa": RC_APPROVED,
    "pm_card_visa_chargeDeclined": RC_DO_NOT_HONOR,
    "pm_card_visa_chargeDeclinedInsufficientFunds": RC_INSUFFICIENT_FUNDS,
    "pm_card_chargeDeclinedExpiredCard": RC_EXPIRED_CARD,
    # Aprueba, pero solo DESPUES de que el comprador se autentique con su banco.
    "pm_card_threeDSecure2Required": RC_APPROVED,
}
REQUIRES_AUTHENTICATION = {"pm_card_threeDSecure2Required"}

# El codigo que queda en last_error_code cuando el comprador no supera 3DS. No
# es un DE 39: la autorizacion ni siquiera llego al emisor.
AUTHENTICATION_FAILED = "AUTH_3DS"


def authorize_mock(
    cvv: str | None,
    expected_cvv: str,
    channel: str,
    rrn: str,
    payment_method_token: str | None = None,
) -> AuthorizationResult:
    """
    Simula la respuesta del emisor.

    Es lo unico falso del sistema, y esta aislado en una funcion para que quede
    evidente donde acaba lo real y empieza el mock. En un adquirente de verdad
    aqui iria el mensaje ISO 8583 0100 hacia la red.

    QR: se aprueba siempre, porque el pagador ya confirmo en su movil.
    Tarjeta presente: el CVV decide, que es lo que permite ensenar el camino de
    rechazo desde el POS.
    Online: decide el token de prueba.
    """
    stan = f"{random.randint(0, 999_999):06d}"

    if channel == "QR":
        return AuthorizationResult(True, RC_APPROVED, _auth_code(), rrn, stan, None)

    if channel == "CARD_NOT_PRESENT":
        code = TEST_PAYMENT_METHODS.get(payment_method_token or "", RC_DO_NOT_HONOR)
        if code == RC_APPROVED:
            return AuthorizationResult(True, RC_APPROVED, _auth_code(), rrn, stan, None)
        return AuthorizationResult(False, code, None, rrn, stan, DECLINE_REASONS[code])

    if cvv == expected_cvv:
        return AuthorizationResult(True, RC_APPROVED, _auth_code(), rrn, stan, None)

    # Un CVV incorrecto en la vida real da 05 (no honrar), no "CVV invalido":
    # el emisor no revela la causa exacta para no ayudar a un atacante.
    return AuthorizationResult(
        False, RC_DO_NOT_HONOR, None, rrn, stan, DECLINE_REASONS[RC_DO_NOT_HONOR]
    )


def _auth_code() -> str:
    """Prefijo MOCK- para que en los datos sea evidente que no es real."""
    return f"MOCK-{random.randint(1000, 9999)}"


def _next_rrn(conn: Connection) -> str:
    """
    RRN (DE 37): fecha YYMMDD + 6 digitos de una secuencia.

    La fecha delante es la convencion de la industria y hace la referencia
    reconocible al buscarla. Los 6 digitos salen de una secuencia y no del azar:
    con azar, unos miles de cobros en un dia ya repiten RRN y la UNIQUE de
    charges tumba el cobro.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT nextval('acquiring.rrn_seq')")
        n = cur.fetchone()[0]
    return datetime.now(timezone.utc).strftime("%y%m%d") + f"{n % 1_000_000:06d}"


# ── Eventos para el comercio ─────────────────────────────────────────────────
def _record_event(
    conn: Connection, merchant_pk: int, event_type: str,
    intent_reference: str, payload: dict, object_type: str = "payment_intent",
) -> None:
    """
    Guarda el evento en webhook_events, en la MISMA transaccion que el cambio.

    Es el patron outbox: si el cobro se confirma, su evento existe; si se
    deshace, tampoco queda evento. Publicar fuera de la transaccion permitiria
    anunciar un pago que luego no existe.

    Los tipos siguen la nomenclatura de Stripe (payment_intent.succeeded,
    payment_intent.payment_failed...). El worker que los ENTREGA al comercio no
    esta escrito: la demo los usa como datos, y llegan al lago por el CDC.
    """
    import json

    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO acquiring.webhook_events
                (merchant_id, event_reference, event_type, object_type, object_id, payload)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (
                merchant_pk, f"evt_{uuid.uuid4().hex[:24]}", event_type, object_type,
                intent_reference, json.dumps(payload, default=str),
            ),
        )


# ── Lectura ──────────────────────────────────────────────────────────────────
def get_merchant(conn: Connection, merchant_id: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, merchant_id, trade_name, mcc, risk_level, status,
                   mdr_bps, fixed_fee_minor, charges_enabled, payout_delay_days,
                   default_currency
            FROM acquiring.merchants WHERE merchant_id = %s
            """,
            (merchant_id,),
        )
        merchant = cur.fetchone()
    if merchant is None:
        raise AcquiringError(f"comercio {merchant_id} no existe", 404)
    return merchant


def get_terminal(conn: Connection, terminal_id: str) -> dict:
    """
    El terminal, con el comercio al que pertenece.

    El TID es unico en toda la red (UNIQUE en terminals), asi que basta para
    saber de que comercio es el cobro. Es como se identifica el POS: ver auth.py.
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT t.id, t.terminal_id, t.device_serial, t.status,
                   m.merchant_id AS merchant_code
            FROM acquiring.terminals t
            JOIN acquiring.merchants m ON m.id = t.merchant_id
            WHERE t.terminal_id = %s
            """,
            (terminal_id,),
        )
        terminal = cur.fetchone()
    if terminal is None:
        raise AcquiringError(f"terminal {terminal_id} no existe", 404)
    if terminal["status"] not in ("ACTIVE", "PROVISIONED"):
        raise AcquiringError(f"terminal {terminal_id} en estado {terminal['status']}", 409)
    return terminal


def get_intent(conn: Connection, intent_reference: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT pi.*, m.trade_name, m.merchant_id AS merchant_code,
                   m.mdr_bps, m.fixed_fee_minor, m.payout_delay_days,
                   -- Viajan hasta el evento: el MCC es la senal de riesgo con la
                   -- que se analiza la operacion, tanto en el lago como en el
                   -- scoring online del Lab 4.
                   m.mcc, m.risk_level
            FROM acquiring.payment_intents pi
            JOIN acquiring.merchants m ON m.id = pi.merchant_id
            WHERE pi.intent_reference = %s
            """,
            (intent_reference,),
        )
        intent = cur.fetchone()
    if intent is None:
        raise AcquiringError(f"intencion {intent_reference} no existe", 404)
    return intent


# ── Escritura ────────────────────────────────────────────────────────────────
# Estados desde los que todavia se puede cobrar. Tras una declinacion la
# intencion VUELVE a REQUIRES_PAYMENT_METHOD, como en Stripe: el comprador
# reintenta con otra tarjeta sobre la misma intencion, en vez de empezar de cero.
_CONFIRMABLE = ("REQUIRES_PAYMENT_METHOD", "REQUIRES_CONFIRMATION")
_CANCELABLE = ("REQUIRES_PAYMENT_METHOD", "REQUIRES_CONFIRMATION", "REQUIRES_ACTION")


def create_intent(
    conn: Connection,
    merchant_id: str,
    terminal: dict | None,
    intent_reference: str,
    amount_minor: int,
    currency_code: str,
    line_items: list[dict],
    statement_description: str | None,
    return_url: str | None = None,
) -> dict:
    """
    Crea la intencion de cobro.

    Con terminal la crea el POS al cerrar el carrito, y nace en
    REQUIRES_CONFIRMATION: el QR ya esta en pantalla esperando al pagador.
    Sin terminal la crea una tienda online, y nace en REQUIRES_PAYMENT_METHOD,
    que es como nace en Stripe: todavia no se sabe con que se va a pagar.
    """
    import json

    merchant = get_merchant(conn, merchant_id)
    if not merchant["charges_enabled"]:
        raise AcquiringError(
            f"el comercio {merchant_id} no tiene cobros habilitados "
            f"(estado {merchant['status']})",
            409,
        )
    status = "REQUIRES_CONFIRMATION" if terminal else "REQUIRES_PAYMENT_METHOD"

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO acquiring.payment_intents
                (intent_reference, merchant_id, terminal_id, amount_minor,
                 currency_code, status, line_items, statement_description,
                 return_url)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (intent_reference) DO NOTHING
            RETURNING id, intent_reference, status, amount_minor,
                      amount_received_minor, currency_code, line_items,
                      channel, last_error_code, created_at
            """,
            (
                intent_reference, merchant["id"], terminal["id"] if terminal else None,
                amount_minor, currency_code, status, json.dumps(line_items),
                statement_description, return_url,
            ),
        )
        row = cur.fetchone()

    if row is None:
        # Ya existia: el cliente reintento. Se devuelve la que hay, que es lo que
        # espera un cliente idempotente. Pero solo si es SUYA: una referencia de
        # otro comercio no se revela ni se reutiliza.
        log.info("intencion %s ya existia, se devuelve la existente", intent_reference)
        existing = get_intent(conn, intent_reference)
        if existing["merchant_code"] != merchant_id:
            raise AcquiringError(f"la referencia {intent_reference} ya esta en uso", 409)
        if existing["amount_minor"] != amount_minor:
            raise AcquiringError(
                f"la intencion {intent_reference} ya existe con otro importe", 409
            )
        return existing | {"trade_name": merchant["trade_name"]}

    _record_event(conn, merchant["id"], "payment_intent.created", intent_reference, {
        "amount_minor": amount_minor, "currency_code": currency_code,
        "status": status, "online": terminal is None,
    })
    return row | {"trade_name": merchant["trade_name"]}


def confirm_intent(
    conn: Connection,
    intent_reference: str,
    channel: str,
    cvv: str | None,
    expected_cvv: str,
    card_token: str | None,
    payment_method: str | None = None,
    authentication_url: str | None = None,
) -> dict:
    """
    Autoriza, captura y asienta en el libro mayor, todo en una transaccion.

    Online, si el emisor pide autenticar, no autoriza todavia: deja la intencion
    en REQUIRES_ACTION con la URL a la que hay que mandar al comprador
    (`authentication_url`, que monta la capa HTTP porque conoce su propio host).

    Devuelve el resultado con los data elements de ISO 8583.
    """
    intent = get_intent(conn, intent_reference)

    if intent["status"] == "SUCCEEDED":
        # Reintento sobre una intencion ya cobrada: se devuelve el cobro que ya
        # existe en vez de cobrar otra vez. Es la defensa de ultimo recurso,
        # ademas de las claves de idempotencia.
        log.info("intencion %s ya estaba cobrada", intent_reference)
        return _existing_charge(conn, intent["id"], intent)

    if intent["status"] == "REQUIRES_ACTION":
        raise AcquiringError(
            f"la intencion {intent_reference} espera la autenticacion del comprador", 409
        )
    if intent["status"] not in _CONFIRMABLE:
        raise AcquiringError(
            f"la intencion {intent_reference} esta en estado {intent['status']}", 409
        )

    token = payment_method if channel == "CARD_NOT_PRESENT" else card_token
    payment_method_pk = _resolve_payment_method(conn, token) if token else None
    if channel == "CARD_NOT_PRESENT" and payment_method_pk is None:
        raise AcquiringError(f"el payment method {payment_method} no existe", 404)

    if channel == "CARD_NOT_PRESENT" and payment_method in REQUIRES_AUTHENTICATION:
        return _require_authentication(
            conn, intent, payment_method_pk, authentication_url
        )

    auth = authorize_mock(cvv, expected_cvv, channel, _next_rrn(conn), token)

    if not auth.approved:
        return _record_decline(conn, intent, channel, auth, payment_method_pk)

    return _record_approval(conn, intent, channel, auth, payment_method_pk)


def _require_authentication(
    conn: Connection, intent: dict, payment_method_pk: int,
    authentication_url: str | None,
) -> dict:
    """
    El emisor pide 3DS: la intencion espera en REQUIRES_ACTION.

    El token de la URL es lo que impide que cualquiera que conozca la referencia
    complete la autenticacion por el comprador. Se guarda en next_action y se
    compara al volver.
    """
    import json

    token = secrets.token_urlsafe(18)
    url = f"{authentication_url or ''}?t={token}"
    next_action = {
        "type": "redirect_to_url",
        "redirect_to_url": {"url": url, "return_url": intent.get("return_url")},
        "token": token,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE acquiring.payment_intents
            SET status = 'REQUIRES_ACTION', channel = 'CARD_NOT_PRESENT',
                payment_method_id = %s, next_action = %s
            WHERE id = %s
            """,
            (payment_method_pk, json.dumps(next_action), intent["id"]),
        )
    _record_event(conn, intent["merchant_id"], "payment_intent.requires_action",
                  intent["intent_reference"], {"type": "redirect_to_url"})

    return {
        "intent_reference": intent["intent_reference"],
        "status": "REQUIRES_ACTION",
        "channel": "CARD_NOT_PRESENT",
        "amount_minor": intent["amount_minor"],
        "currency_code": intent["currency_code"],
        "next_action": {k: v for k, v in next_action.items() if k != "token"},
    }


def complete_authentication(
    conn: Connection, intent_reference: str, token: str, authenticated: bool,
) -> dict:
    """
    El comprador vuelve de su banco. Si se autentico, se autoriza; si no, la
    intencion vuelve a REQUIRES_PAYMENT_METHOD para que pruebe otra tarjeta.

    Un 3DS fallido NO crea cobro: la autorizacion ni siquiera llego al emisor.
    Por eso queda solo en la intencion (last_error_code) y en su evento.
    """
    import hmac

    intent = get_intent(conn, intent_reference)
    action = intent.get("next_action") or {}
    if intent["status"] != "REQUIRES_ACTION" or not action.get("token"):
        raise AcquiringError(
            f"la intencion {intent_reference} no espera autenticacion", 409
        )
    if not hmac.compare_digest(action["token"], token):
        raise AcquiringError("token de autenticacion invalido", 403)

    return_url = (action.get("redirect_to_url") or {}).get("return_url")

    if not authenticated:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE acquiring.payment_intents
                SET status = 'REQUIRES_PAYMENT_METHOD', next_action = NULL,
                    last_error_code = %s
                WHERE id = %s
                """,
                (AUTHENTICATION_FAILED, intent["id"]),
            )
        _record_event(conn, intent["merchant_id"], "payment_intent.payment_failed",
                      intent_reference, {"reason": "authentication_failed"})
        return {
            "intent_reference": intent_reference,
            "status": "REQUIRES_PAYMENT_METHOD",
            "channel": "CARD_NOT_PRESENT",
            "amount_minor": intent["amount_minor"],
            "currency_code": intent["currency_code"],
            "decline_reason": "AUTHENTICATION_FAILED",
            "return_url": return_url,
        }

    auth = authorize_mock(None, "", "CARD_NOT_PRESENT", _next_rrn(conn),
                          "pm_card_threeDSecure2Required")
    charge = _record_approval(conn, intent, "CARD_NOT_PRESENT", auth,
                              intent.get("payment_method_id"))
    return charge | {"return_url": return_url}


def cancel_intent(conn: Connection, intent_reference: str, reason: str) -> dict:
    """
    Cancela una intencion que todavia no se cobro.

    Es un dato de negocio, no limpieza: una cancelacion por abandono es la que
    alimenta el embudo de conversion de la tienda.
    """
    intent = get_intent(conn, intent_reference)
    if intent["status"] == "CANCELED":
        return intent
    if intent["status"] not in _CANCELABLE:
        raise AcquiringError(
            f"la intencion {intent_reference} esta en estado {intent['status']} "
            f"y no se puede cancelar",
            409,
        )
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE acquiring.payment_intents
            SET status = 'CANCELED', canceled_at = NOW(),
                cancellation_reason = %s, next_action = NULL
            WHERE id = %s
            """,
            (reason, intent["id"]),
        )
    _record_event(conn, intent["merchant_id"], "payment_intent.canceled",
                  intent_reference, {"cancellation_reason": reason})
    return get_intent(conn, intent_reference)


def refund(
    conn: Connection, merchant_id: str, intent_reference: str,
    amount_minor: int | None, reason: str,
) -> dict:
    """
    Devuelve, total o parcialmente, lo cobrado en una intencion.

    Como en Stripe: el cobro NO cambia de estado, se le asocia una devolucion y
    acumula amount_refunded. La base garantiza que nunca se devuelva mas de lo
    capturado (charges_refund_within_capture). La comision no se devuelve: el
    asiento negativo del libro es el bruto, y el MDR ya cobrado se queda.

    Para el lago es la operacion mas interesante del sistema: un UPDATE sobre un
    cobro que puede tener dias. Un incremental que pregunta "que hay nuevo desde
    ayer" no la ve; el CDC si.
    """
    intent = get_intent(conn, intent_reference)
    if intent["merchant_code"] != merchant_id:
        raise AcquiringError(f"intencion {intent_reference} no existe", 404)

    with conn.cursor(row_factory=dict_row) as cur:
        # FOR UPDATE: dos devoluciones simultaneas del mismo cobro no pueden
        # leer el mismo saldo y devolver dos veces lo mismo.
        cur.execute(
            """
            SELECT id, charge_reference, amount_captured_minor, amount_refunded_minor,
                   currency_code
            FROM acquiring.charges
            WHERE payment_intent_id = %s AND status = 'CAPTURED'
            ORDER BY id DESC LIMIT 1
            FOR UPDATE
            """,
            (intent["id"],),
        )
        charge = cur.fetchone()
    if charge is None:
        raise AcquiringError(f"la intencion {intent_reference} no tiene un cobro capturado", 409)

    disponible = charge["amount_captured_minor"] - charge["amount_refunded_minor"]
    importe = disponible if amount_minor is None else amount_minor
    if disponible <= 0:
        raise AcquiringError(f"el cobro {charge['charge_reference']} ya esta devuelto", 409)
    if importe > disponible:
        raise AcquiringError(
            f"no se puede devolver {importe}: quedan {disponible} por devolver", 409
        )

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT count(*) AS n FROM acquiring.refunds WHERE charge_id = %s",
                    (charge["id"],))
        n = cur.fetchone()["n"] + 1
        referencia = f"re_{intent_reference.removeprefix('pi_')}_{n}"
        cur.execute(
            """
            INSERT INTO acquiring.refunds
                (refund_reference, charge_id, merchant_id, amount_minor, currency_code,
                 reason, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'SUCCEEDED')
            RETURNING id, refund_reference, created_at
            """,
            (referencia, charge["id"], intent["merchant_id"], importe,
             charge["currency_code"], reason),
        )
        devolucion = cur.fetchone()
        cur.execute(
            "UPDATE acquiring.charges SET amount_refunded_minor = amount_refunded_minor + %s "
            "WHERE id = %s RETURNING amount_refunded_minor",
            (importe, charge["id"]),
        )
        total_devuelto = cur.fetchone()["amount_refunded_minor"]
        # El asiento: sale dinero del comercio. Sin comision (fee 0): la del
        # cobro original no se devuelve.
        cur.execute(
            """
            INSERT INTO acquiring.balance_transactions
                (merchant_id, source_type, refund_id, gross_minor, fee_minor, net_minor,
                 currency_code, status, available_on)
            VALUES (%s, 'REFUND', %s, %s, 0, %s, %s, 'PENDING', CURRENT_DATE)
            """,
            (intent["merchant_id"], devolucion["id"], -importe, -importe, charge["currency_code"]),
        )
    _record_event(conn, intent["merchant_id"], "charge.refunded", charge["charge_reference"], {
        "refund_reference": devolucion["refund_reference"], "amount_minor": importe,
        "amount_refunded_minor": total_devuelto, "reason": reason,
    }, object_type="charge")

    return {
        "refund_reference": devolucion["refund_reference"],
        "intent_reference": intent_reference,
        "charge_reference": charge["charge_reference"],
        "amount_minor": importe,
        "currency_code": charge["currency_code"],
        "status": "SUCCEEDED",
        "reason": reason,
        "charge_amount_refunded_minor": total_devuelto,
        "charge_fully_refunded": total_devuelto == charge["amount_captured_minor"],
        "created_at": devolucion["created_at"],
    }


def _resolve_payment_method(conn: Connection, card_token: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM acquiring.payment_methods WHERE processor_token = %s",
            (card_token,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _charge_reference(conn: Connection, intent: dict) -> str:
    """
    ch_<ref>_<n>: un cobro por intento.

    Antes el rechazo era siempre `_d`, y con reintentos sobre la misma intencion
    el segundo rechazo chocaba con la UNIQUE del primero.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM acquiring.charges WHERE payment_intent_id = %s",
            (intent["id"],),
        )
        intento = cur.fetchone()[0] + 1
    return f"ch_{intent['intent_reference'].removeprefix('pi_')}_{intento}"


def _record_decline(
    conn: Connection, intent: dict, channel: str,
    auth: AuthorizationResult, payment_method_pk: int | None,
) -> dict:
    """Un rechazo tambien se persiste: es un dato de negocio, no un error."""
    charge_reference = _charge_reference(conn, intent)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO acquiring.charges
                (charge_reference, payment_intent_id, merchant_id, terminal_id,
                 payment_method_id, amount_minor, currency_code, status, channel,
                 mti, stan, rrn, response_code, failure_code, failure_message)
            VALUES (%s,%s,%s,%s,%s,%s,%s,'FAILED',%s,'0110',%s,%s,%s,%s,%s)
            RETURNING charge_reference
            """,
            (
                charge_reference, intent["id"], intent["merchant_id"],
                intent["terminal_id"], payment_method_pk, intent["amount_minor"],
                intent["currency_code"], channel, auth.stan, auth.rrn,
                auth.response_code, auth.decline_reason,
                f"Rechazada por el emisor ({auth.response_code})",
            ),
        )
        charge = cur.fetchone()
        # Vuelve a pedir medio de pago, como en Stripe: el reintento va sobre la
        # misma intencion. El POS no lo nota: crea una intencion por venta.
        cur.execute(
            """
            UPDATE acquiring.payment_intents
            SET status = 'REQUIRES_PAYMENT_METHOD', last_error_code = %s,
                channel = %s
            WHERE id = %s
            """,
            (auth.response_code, channel, intent["id"]),
        )
    _record_event(conn, intent["merchant_id"], "payment_intent.payment_failed",
                  intent["intent_reference"], {
                      "charge_reference": charge["charge_reference"],
                      "response_code": auth.response_code, "channel": channel,
                  })

    return {
        "intent_reference": intent["intent_reference"],
        "charge_reference": charge["charge_reference"],
        "status": "FAILED",
        "channel": channel,
        "response_code": auth.response_code,
        "auth_code": None,
        "rrn": auth.rrn,
        "stan": auth.stan,
        "amount_minor": intent["amount_minor"],
        "currency_code": intent["currency_code"],
        "fee_minor": None,
        "net_minor": None,
        "decline_reason": auth.decline_reason,
    }


def _record_approval(
    conn: Connection, intent: dict, channel: str,
    auth: AuthorizationResult, payment_method_pk: int | None,
) -> dict:
    """Autorizacion aprobada: se captura y se asienta en el libro."""
    charge_reference = _charge_reference(conn, intent)
    amount = intent["amount_minor"]

    # La comision, desde el MDR del comercio leido en esta transaccion.
    fee = (amount * intent["mdr_bps"]) // 10_000 + intent["fixed_fee_minor"]
    net = amount - fee
    available_on = date.today()
    if intent["payout_delay_days"]:
        from datetime import timedelta
        available_on = available_on + timedelta(days=intent["payout_delay_days"])

    with conn.cursor(row_factory=dict_row) as cur:
        # capture_method AUTOMATIC: se autoriza y captura de una vez, que es lo
        # normal en retail presencial.
        cur.execute(
            """
            INSERT INTO acquiring.charges
                (charge_reference, payment_intent_id, merchant_id, terminal_id,
                 payment_method_id, amount_minor, amount_captured_minor,
                 currency_code, status, channel, mti, stan, rrn,
                 response_code, auth_code, captured_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'CAPTURED',%s,'0110',%s,%s,%s,%s,NOW())
            RETURNING id, charge_reference
            """,
            (
                charge_reference, intent["id"], intent["merchant_id"],
                intent["terminal_id"], payment_method_pk, amount, amount,
                intent["currency_code"], channel, auth.stan, auth.rrn,
                auth.response_code, auth.auth_code,
            ),
        )
        charge = cur.fetchone()

        # El asiento. La constraint de la base garantiza net = gross - fee.
        cur.execute(
            """
            INSERT INTO acquiring.balance_transactions
                (merchant_id, source_type, charge_id, gross_minor, fee_minor,
                 net_minor, currency_code, status, available_on)
            VALUES (%s,'CHARGE',%s,%s,%s,%s,%s,'PENDING',%s)
            """,
            (
                intent["merchant_id"], charge["id"], amount, fee, net,
                intent["currency_code"], available_on,
            ),
        )

        cur.execute(
            """
            UPDATE acquiring.payment_intents
            SET status = 'SUCCEEDED', amount_received_minor = %s,
                channel = %s, payment_method_id = COALESCE(%s, payment_method_id),
                next_action = NULL
            WHERE id = %s
            """,
            (amount, channel, payment_method_pk, intent["id"]),
        )
    _record_event(conn, intent["merchant_id"], "payment_intent.succeeded",
                  intent["intent_reference"], {
                      "charge_reference": charge["charge_reference"],
                      "amount_minor": amount, "fee_minor": fee, "net_minor": net,
                      "channel": channel,
                  })

    return {
        "intent_reference": intent["intent_reference"],
        "charge_reference": charge["charge_reference"],
        "status": "CAPTURED",
        "channel": channel,
        "response_code": auth.response_code,
        "auth_code": auth.auth_code,
        "rrn": auth.rrn,
        "stan": auth.stan,
        "amount_minor": amount,
        "currency_code": intent["currency_code"],
        "fee_minor": fee,
        "net_minor": net,
        "decline_reason": None,
    }


def _existing_charge(conn: Connection, intent_pk: int, intent: dict) -> dict:
    """Devuelve el cobro que ya existe para una intencion ya cobrada."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT c.charge_reference, c.status, c.response_code, c.auth_code,
                   c.rrn, c.stan, c.amount_minor, c.currency_code, c.channel,
                   bt.fee_minor, bt.net_minor
            FROM acquiring.charges c
            LEFT JOIN acquiring.balance_transactions bt
                   ON bt.charge_id = c.id AND bt.source_type = 'CHARGE'
            WHERE c.payment_intent_id = %s AND c.status = 'CAPTURED'
            ORDER BY c.created_at DESC LIMIT 1
            """,
            (intent_pk,),
        )
        row = cur.fetchone()
    if row is None:
        raise AcquiringError("la intencion esta cobrada pero no se encuentra el cobro", 500)
    return {
        "intent_reference": intent["intent_reference"],
        "decline_reason": None,
        # Marca que esto NO es un cobro nuevo: es el que ya existia. Quien
        # publica eventos tiene que saberlo para no anunciar dos veces la misma
        # operacion.
        "already_captured": True,
        **row,
    }


def get_terminal_context(conn: Connection, terminal_pk: int | None) -> dict:
    """
    Los datos del terminal que necesita el evento: su codigo y su serial.

    El serial no es decorativo: es el clientId con el que ese POS se autentica
    en IoT Core, y de el cuelga el topic donde se publica el evento.
    """
    if terminal_pk is None:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT terminal_id AS terminal_code, device_serial
            FROM acquiring.terminals WHERE id = %s
            """,
            (terminal_pk,),
        )
        return cur.fetchone() or {}
