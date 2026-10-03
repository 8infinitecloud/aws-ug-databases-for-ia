"""
Lab 4 — Scoring de riesgo online.

La diferencia con los labs 1-3 no es la velocidad: es la pregunta. Los tres
primeros responden "que paso"; este responde "podemos reaccionar ahora". Y eso
cambia la arquitectura entera, no solo el tamano del lote.

El evento llega de IoT Core, se evalua contra contadores de ventana en DynamoDB
y sale una decision en milisegundos. No hay S3 en el camino critico, porque S3
es almacenamiento y aqui lo que hace falta es estado con lectura inmediata.

Las reglas son *velocity checks*: controles de fraude reales de un adquirente.
No detectan fraude por si solas —eso lo hace el emisor— pero detectan patrones
que un comercio legitimo no produce, y son lo que permite cortar una racha de
card testing antes de que se convierta en cien contracargos.

Las funciones de arriba son puras: reciben el evento y los contadores, y
devuelven alertas. Toda la E/S vive en el handler. Asi las reglas se prueban sin
AWS, que es como se itera sobre ellas.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone

log = logging.getLogger()
log.setLevel(logging.INFO)

# ── Umbrales ─────────────────────────────────────────────────────────────────
# Por entorno para poder ajustarlos desde la consola antes de la charla, sin
# redesplegar la Lambda ni reflashear el POS.

# La ventana. Un minuto es corto a proposito: el card testing es una rafaga, y
# una ventana larga la diluye entre operaciones legitimas.
WINDOW_SECONDS = int(os.getenv("WINDOW_SECONDS", "60"))

# Cuantas operaciones seguidas del mismo terminal son ya sospechosas.
MAX_TX_PER_WINDOW = int(os.getenv("MAX_TX_PER_WINDOW", "5"))

# Monto acumulado en la ventana, en la moneda del evento.
MAX_AMOUNT_PER_WINDOW = float(os.getenv("MAX_AMOUNT_PER_WINDOW", "500"))

# Proporcion de declinaciones que enciende la alarma, y el minimo de muestras
# para no disparar con la primera declinacion del dia.
MAX_DECLINE_RATIO = float(os.getenv("MAX_DECLINE_RATIO", "0.5"))
MIN_SAMPLE_FOR_RATIO = int(os.getenv("MIN_SAMPLE_FOR_RATIO", "4"))

# Importe que, en un comercio de riesgo alto, merece revision manual.
HIGH_RISK_AMOUNT = float(os.getenv("HIGH_RISK_AMOUNT", "300"))

# Los umbrales de una TIENDA ONLINE. Sus contadores van por comercio y no por
# terminal (no hay terminal), asi que suman a todos sus compradores a la vez: 5
# compras en un minuto es una tarde normal para una tienda, no card testing. Y
# sus tickets son otros: un portatil cuesta mas que los 500 de la ventana de un
# POS de cafeteria, y con ese umbral cada venta de electronica saltaria.
#
# El ratio de declinaciones NO cambia: el card testing online —muchas tarjetas
# robadas probadas contra la misma tienda— es justo el patron que mide.
MAX_TX_PER_WINDOW_ONLINE = int(os.getenv("MAX_TX_PER_WINDOW_ONLINE", "30"))
MAX_AMOUNT_PER_WINDOW_ONLINE = float(os.getenv("MAX_AMOUNT_PER_WINDOW_ONLINE", "20000"))

ONLINE = "CARD_NOT_PRESENT"

# ISO 8583 DE 39: solo el 00 aprueba.
RESPONSE_CODE_APPROVED = "00"


@dataclass
class Alert:
    """Una regla que salto. No bloquea el cobro: lo marca."""

    rule: str
    severity: str          # LOW | MEDIUM | HIGH
    message: str
    observed: float
    threshold: float


@dataclass
class Counters:
    """El estado de la ventana para un terminal."""

    transactions: int = 0
    amount: float = 0.0
    declines: int = 0

    @property
    def decline_ratio(self) -> float:
        return self.declines / self.transactions if self.transactions else 0.0


@dataclass
class Decision:
    """Lo que sale del scoring."""

    transaction_id: str
    merchant_id: str
    terminal_id: str | None
    flagged: bool
    alerts: list[Alert] = field(default_factory=list)
    counters: Counters = field(default_factory=Counters)
    scored_at: str = ""


# ── Las reglas, puras ────────────────────────────────────────────────────────
def is_online(event: dict) -> bool:
    """Un pago sin terminal: lo hizo una tienda online."""
    return event.get("payment_method") == ONLINE or not event.get("pos_id")


def rule_velocity_count(event: dict, c: Counters) -> Alert | None:
    """
    Demasiadas operaciones seguidas en el mismo terminal (o tienda online).

    Un POS de cafeteria hace una venta cada varios minutos. Veinte en un minuto
    no es un dia bueno: es alguien probando tarjetas.
    """
    limite = MAX_TX_PER_WINDOW_ONLINE if is_online(event) else MAX_TX_PER_WINDOW
    if c.transactions <= limite:
        return None
    return Alert(
        rule="VELOCITY_COUNT",
        severity="HIGH",
        message=f"{c.transactions} operaciones en {WINDOW_SECONDS}s",
        observed=float(c.transactions),
        threshold=float(limite),
    )


def rule_velocity_amount(event: dict, c: Counters) -> Alert | None:
    """Monto acumulado inusual para la ventana."""
    limite = MAX_AMOUNT_PER_WINDOW_ONLINE if is_online(event) else MAX_AMOUNT_PER_WINDOW
    if c.amount <= limite:
        return None
    return Alert(
        rule="VELOCITY_AMOUNT",
        severity="MEDIUM",
        message=f"{c.amount:.2f} acumulado en {WINDOW_SECONDS}s",
        observed=round(c.amount, 2),
        threshold=limite,
    )


def rule_decline_ratio(event: dict, c: Counters) -> Alert | None:
    """
    Muchas declinaciones seguidas: la firma del card testing.

    Quien prueba tarjetas robadas acumula rechazos, porque la mayoria ya estan
    bloqueadas. Un comercio legitimo no produce ese patron.

    Pide un minimo de muestras: con dos operaciones, una declinacion da el 50% y
    no significa nada.
    """
    if c.transactions < MIN_SAMPLE_FOR_RATIO:
        return None
    if c.decline_ratio <= MAX_DECLINE_RATIO:
        return None
    return Alert(
        rule="DECLINE_RATIO",
        severity="HIGH",
        message=f"{c.declines}/{c.transactions} declinadas ({c.decline_ratio:.0%})",
        observed=round(c.decline_ratio, 2),
        threshold=MAX_DECLINE_RATIO,
    )


def rule_high_risk_mcc(event: dict, c: Counters) -> Alert | None:
    """
    Importe alto en un comercio de riesgo alto.

    El MCC dice a que se dedica el comercio, y el nivel de riesgo se asigna al
    afiliarlo. Un importe grande en una casa de cambio no es lo mismo que en una
    cafeteria: es la combinacion lo que importa, no el importe solo.
    """
    if event.get("risk_level") != "HIGH":
        return None
    amount = float(event.get("amount") or 0)
    if amount <= HIGH_RISK_AMOUNT:
        return None
    return Alert(
        rule="HIGH_RISK_MCC",
        severity="MEDIUM",
        message=f"{amount:.2f} en MCC {event.get('mcc')} de riesgo alto",
        observed=amount,
        threshold=HIGH_RISK_AMOUNT,
    )


RULES = (
    rule_velocity_count,
    rule_velocity_amount,
    rule_decline_ratio,
    rule_high_risk_mcc,
)


def score(event: dict, counters: Counters) -> Decision:
    """Evalua todas las reglas. Sin E/S: esto es lo que se prueba en local."""
    alerts = [a for a in (rule(event, counters) for rule in RULES) if a is not None]

    return Decision(
        transaction_id=event.get("transaction_id", ""),
        merchant_id=event.get("merchant_id", ""),
        terminal_id=event.get("pos_id"),
        flagged=bool(alerts),
        alerts=alerts,
        counters=counters,
        scored_at=datetime.now(timezone.utc).isoformat(),
    )


def window_key(timestamp: str | None = None, window_seconds: int | None = None) -> str:
    """
    El identificador de la ventana a la que pertenece un evento.

    Ventanas fijas y no deslizantes: una deslizante exige guardar cada evento y
    recontar, y aqui basta con un contador por cubo. La contrapartida es que un
    ataque a caballo entre dos cubos se reparte; para eso estan las otras reglas.
    """
    w = window_seconds or WINDOW_SECONDS
    if timestamp:
        try:
            momento = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            momento = datetime.now(timezone.utc)
    else:
        momento = datetime.now(timezone.utc)

    epoch = int(momento.timestamp())
    return str(epoch - (epoch % w))


def partition_key(event: dict) -> str:
    """
    Los contadores van por terminal, no por comercio.

    Un comercio con diez cajas mueve diez veces mas que uno con una, y agregarlo
    por comercio haria que el umbral no significara lo mismo en los dos. El
    terminal es la unidad donde el patron es comparable.

    Una tienda online no tiene terminal: su unidad es el comercio, y se marca
    con otro prefijo (M#) para que en DynamoDB no se confunda con un terminal y
    se lea con sus propios umbrales.
    """
    if event.get("pos_id"):
        return f"T#{event['pos_id']}"
    return f"M#{event.get('merchant_id') or 'DESCONOCIDO'}"


# ── El handler ───────────────────────────────────────────────────────────────
def _table():
    """Se importa boto3 aqui para que las reglas se puedan probar sin AWS."""
    import boto3

    return boto3.resource("dynamodb").Table(os.environ["VELOCITY_TABLE"])


def bump_counters(table, event: dict) -> Counters:
    """
    Incrementa los contadores de la ventana y devuelve los nuevos valores.

    Es un UpdateItem atomico con ADD: dos eventos simultaneos del mismo terminal
    no se pisan. Leer, sumar y escribir —que es lo que se hace sin pensarlo—
    perderia operaciones justo cuando llegan en rafaga, que es exactamente el
    caso que hay que detectar.
    """
    declinada = 1 if event.get("response_code") != RESPONSE_CODE_APPROVED else 0
    # El TTL limpia solo: un contador de hace una hora no le importa a nadie y
    # pagar por almacenarlo seria absurdo.
    expira = int(datetime.now(timezone.utc).timestamp()) + WINDOW_SECONDS * 10

    resp = table.update_item(
        Key={"pk": partition_key(event), "sk": f"W#{window_key(event.get('timestamp'))}"},
        UpdateExpression=(
            "ADD tx_count :uno, amount_total :monto, declines :dec "
            "SET expires_at = :ttl"
        ),
        ExpressionAttributeValues={
            ":uno": 1,
            ":monto": _decimal(event.get("amount")),
            ":dec": declinada,
            ":ttl": expira,
        },
        ReturnValues="ALL_NEW",
    )

    item = resp["Attributes"]
    return Counters(
        transactions=int(item.get("tx_count", 0)),
        amount=float(item.get("amount_total", 0)),
        declines=int(item.get("declines", 0)),
    )


def _decimal(valor):
    """DynamoDB no acepta float: hay que pasarle Decimal."""
    from decimal import Decimal

    return Decimal(str(valor or 0))


def save_alert(table, decision: Decision, event: dict) -> None:
    """Guarda la operacion marcada, para que el dashboard la lea."""
    table.put_item(
        Item={
            "pk": partition_key(event),
            "sk": f"ALERT#{decision.scored_at}#{decision.transaction_id}",
            "transaction_id": decision.transaction_id,
            "merchant_id": decision.merchant_id,
            "amount": _decimal(event.get("amount")),
            "mcc": event.get("mcc"),
            "status": event.get("status"),
            "response_code": event.get("response_code"),
            "rules": [a.rule for a in decision.alerts],
            "severity": max(a.severity for a in decision.alerts),
            "detail": json.dumps([asdict(a) for a in decision.alerts], default=str),
            "scored_at": decision.scored_at,
            # Las alertas duran mas que los contadores: son el resultado, no el
            # estado intermedio.
            "expires_at": int(datetime.now(timezone.utc).timestamp()) + 86400,
        }
    )


def lambda_handler(event, context):
    """
    Lo invoca la regla de IoT Core, un evento por invocacion.

    No devuelve error aunque falle: una excepcion haria que IoT reintentara, y
    reintentar un scoring ya contado inflaria los contadores y dispararia
    alertas falsas. El cobro ya ocurrio; esto solo lo observa.
    """
    log.info("evento: %s", json.dumps(event, default=str))

    try:
        table = _table()
        counters = bump_counters(table, event)
        decision = score(event, counters)

        if decision.flagged:
            save_alert(table, decision, event)
            log.warning(
                "MARCADA %s | %s | %s",
                decision.transaction_id,
                ", ".join(a.rule for a in decision.alerts),
                "; ".join(a.message for a in decision.alerts),
            )
        else:
            log.info(
                "ok %s | %d tx / %.2f en la ventana",
                decision.transaction_id,
                counters.transactions,
                counters.amount,
            )

        return {"flagged": decision.flagged,
                "rules": [a.rule for a in decision.alerts]}

    except Exception:  # noqa: BLE001 — se registra y se sigue, ver docstring
        log.exception("fallo el scoring; el evento se descarta")
        return {"flagged": False, "error": True}
