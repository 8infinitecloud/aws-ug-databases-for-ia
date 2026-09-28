"""
Publicacion de eventos a AWS IoT Core.

El evento es el contrato con la plataforma de datos: sea el pago por tarjeta o
por QR, lo que sale de aqui tiene la misma forma. Es lo que permite que el
pipeline de Glue no tenga que saber de donde vino el pago.

Si no hay IOT_ENDPOINT configurado, el evento se registra en el log y no se
publica. Asi el backend funciona en local sin IoT Core.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

log = logging.getLogger(__name__)

EVENT_VERSION = "1.0"


def build_payment_event(
    event_type: str,
    intent_reference: str,
    charge: dict,
    merchant_code: str,
    terminal_code: str | None,
    device_serial: str | None,
    line_items: list[dict] | None = None,
) -> dict:
    """
    Construye el evento normalizado.

    Los importes van en unidades MAYORES (18.50) y no menores: el consumidor es
    la plataforma de analitica, que espera importes legibles. La base de datos
    guarda centimos; el evento publica soles.
    """
    return {
        "event_type": event_type,
        "event_version": EVENT_VERSION,
        "transaction_id": charge.get("charge_reference") or intent_reference,
        "intent_reference": intent_reference,
        "merchant_id": merchant_code,
        "pos_id": terminal_code,
        "device_id": device_serial,
        "payment_method": charge.get("channel") or charge.get("payment_method"),
        "amount": round(charge["amount_minor"] / 100, 2),
        "currency": charge["currency_code"],
        "status": charge["status"],
        # Data elements de ISO 8583: viajan hasta el data lake, porque son los
        # que permiten rastrear una operacion desde una reclamacion.
        "response_code": charge.get("response_code"),
        "auth_code": charge.get("auth_code"),
        "rrn": charge.get("rrn"),
        "stan": charge.get("stan"),
        # La economia de la operacion: esto es lo que hace que el dato sea util
        # para el adquirente y no solo para el comercio.
        "fee_amount": round(charge["fee_minor"] / 100, 2) if charge.get("fee_minor") is not None else None,
        "net_amount": round(charge["net_minor"] / 100, 2) if charge.get("net_minor") is not None else None,
        "decline_reason": charge.get("decline_reason"),
        "items": line_items or [],
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


class EventPublisher:
    """Publica en IoT Core, o en el log si no hay endpoint configurado."""

    def __init__(self, endpoint: str, region: str, topic_prefix: str):
        self.endpoint = endpoint
        self.region = region
        self.topic_prefix = topic_prefix
        self._client = None

    @property
    def enabled(self) -> bool:
        return bool(self.endpoint)

    def _get_client(self):
        if self._client is None:
            import boto3

            self._client = boto3.client(
                "iot-data", region_name=self.region, endpoint_url=f"https://{self.endpoint}"
            )
        return self._client

    def publish(self, event: dict, device_serial: str | None = None) -> bool:
        """
        Publica el evento. Devuelve True si salio.

        No lanza excepcion si falla: un fallo publicando NO debe deshacer un
        cobro que ya se hizo. Se registra y se sigue; el dato esta en la base y
        se puede reprocesar.
        """
        # El topic cuelga del serial del dispositivo, que es el clientId con el
        # que ese POS se autentica en IoT Core.
        root = device_serial or self.topic_prefix
        topic = f"{root}/events"

        if not self.enabled:
            log.info("IOT_ENDPOINT sin configurar; evento no publicado en %s: %s",
                     topic, json.dumps(event, default=str))
            return False

        try:
            self._get_client().publish(
                topic=topic, qos=1, payload=json.dumps(event, default=str)
            )
            log.info("evento %s publicado en %s", event["event_type"], topic)
            return True
        except Exception as exc:  # noqa: BLE001
            log.error("no se pudo publicar en %s: %s", topic, exc)
            return False
