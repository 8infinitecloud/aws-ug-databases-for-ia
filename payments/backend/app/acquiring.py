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
def authorize_mock(cvv: str | None, expected_cvv: str, channel: str) -> AuthorizationResult:
    """
    Simula la respuesta del emisor.

    Es lo unico falso del sistema, y esta aislado en una funcion para que quede
    evidente donde acaba lo real y empieza el mock. En un adquirente de verdad
    aqui iria el mensaje ISO 8583 0100 hacia la red.

    QR: se aprueba siempre, porque el pagador ya confirmo en su movil.
    Tarjeta: el CVV decide, que es lo que permite ensenar el camino de rechazo.
    """
    stan = f"{random.randint(0, 999_999):06d}"
    # El RRN lleva la fecha: es la convencion de la industria y hace que la
    # referencia sea reconocible al buscarla.
    rrn = datetime.now(timezone.utc).strftime("%y%m%d") + f"{random.randint(0, 999_999):06d}"

    if channel == "QR":
        return AuthorizationResult(True, RC_APPROVED, _auth_code(), rrn, stan, None)

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


def get_terminal(conn: Connection, merchant_pk: int, terminal_id: str) -> dict:
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT id, terminal_id, device_serial, status
            FROM acquiring.terminals
            WHERE merchant_id = %s AND terminal_id = %s
            """,
            (merchant_pk, terminal_id),
        )
        terminal = cur.fetchone()
    if terminal is None:
        raise AcquiringError(f"terminal {terminal_id} no existe para ese comercio", 404)
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
def create_intent(
    conn: Connection,
    merchant_id: str,
    terminal_id: str,
    intent_reference: str,
    amount_minor: int,
    currency_code: str,
    line_items: list[dict],
    statement_description: str | None,
) -> dict:
    """Crea la intencion de cobro. El POS la llama al cerrar el carrito."""
    import json

    merchant = get_merchant(conn, merchant_id)
    if not merchant["charges_enabled"]:
        raise AcquiringError(
            f"el comercio {merchant_id} no tiene cobros habilitados "
            f"(estado {merchant['status']})",
            409,
        )
    terminal = get_terminal(conn, merchant["id"], terminal_id)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO acquiring.payment_intents
                (intent_reference, merchant_id, terminal_id, amount_minor,
                 currency_code, status, line_items, statement_description)
            VALUES (%s, %s, %s, %s, %s, 'REQUIRES_CONFIRMATION', %s, %s)
            ON CONFLICT (intent_reference) DO NOTHING
            RETURNING id, intent_reference, status, amount_minor,
                      amount_received_minor, currency_code, line_items, created_at
            """,
            (
                intent_reference, merchant["id"], terminal["id"], amount_minor,
                currency_code, json.dumps(line_items), statement_description,
            ),
        )
        row = cur.fetchone()

    if row is None:
        # Ya existia: el POS reintento. Se devuelve la que hay, que es lo que
        # espera un cliente idempotente.
        log.info("intencion %s ya existia, se devuelve la existente", intent_reference)
        existing = get_intent(conn, intent_reference)
        if existing["amount_minor"] != amount_minor:
            raise AcquiringError(
                f"la intencion {intent_reference} ya existe con otro importe", 409
            )
        return existing | {"trade_name": merchant["trade_name"]}

    return row | {"trade_name": merchant["trade_name"]}


def confirm_intent(
    conn: Connection,
    intent_reference: str,
    channel: str,
    cvv: str | None,
    expected_cvv: str,
    card_token: str | None,
) -> dict:
    """
    Autoriza, captura y asienta en el libro mayor, todo en una transaccion.

    Devuelve el resultado con los data elements de ISO 8583.
    """
    intent = get_intent(conn, intent_reference)

    if intent["status"] == "SUCCEEDED":
        # Reintento sobre una intencion ya cobrada: se devuelve el cobro que ya
        # existe en vez de cobrar otra vez. Es la defensa de ultimo recurso,
        # ademas de las claves de idempotencia.
        log.info("intencion %s ya estaba cobrada", intent_reference)
        return _existing_charge(conn, intent["id"], intent)

    if intent["status"] in ("CANCELED", "FAILED"):
        raise AcquiringError(
            f"la intencion {intent_reference} esta en estado {intent['status']}", 409
        )

    auth = authorize_mock(cvv, expected_cvv, channel)
    payment_method_pk = _resolve_payment_method(conn, card_token) if card_token else None

    if not auth.approved:
        return _record_decline(conn, intent, channel, auth, payment_method_pk)

    return _record_approval(conn, intent, channel, auth, payment_method_pk)


def _resolve_payment_method(conn: Connection, card_token: str) -> int | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM acquiring.payment_methods WHERE processor_token = %s",
            (card_token,),
        )
        row = cur.fetchone()
    return row[0] if row else None


def _record_decline(
    conn: Connection, intent: dict, channel: str,
    auth: AuthorizationResult, payment_method_pk: int | None,
) -> dict:
    """Un rechazo tambien se persiste: es un dato de negocio, no un error."""
    charge_reference = f"ch_{intent['intent_reference'].removeprefix('pi_')}_d"
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
        # La intencion queda FAILED, pero se puede reintentar creando otra.
        cur.execute(
            """
            UPDATE acquiring.payment_intents
            SET status = 'FAILED', last_error_code = %s
            WHERE id = %s
            """,
            (auth.response_code, intent["id"]),
        )

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
    charge_reference = f"ch_{intent['intent_reference'].removeprefix('pi_')}_1"
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
                channel = %s, payment_method_id = COALESCE(%s, payment_method_id)
            WHERE id = %s
            """,
            (amount, channel, payment_method_pk, intent["id"]),
        )

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
