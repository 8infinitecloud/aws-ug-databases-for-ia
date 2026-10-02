"""Contratos de la API. Pydantic valida en el borde, no dentro del dominio."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class LineItem(BaseModel):
    """Lo que compro el cliente. Al pago no le hace falta; la analitica si."""

    sku: str = Field(max_length=32)
    name: str = Field(max_length=120)
    quantity: int = Field(gt=0, le=99)
    unit_price_minor: int = Field(gt=0)


class CreateIntentRequest(BaseModel):
    """
    Pide una intencion de cobro.

    El POS la pide con su terminal al cerrar el carrito. Una tienda online la
    pide sin terminal, autenticada con su clave (ver auth.py).
    """

    # La genera el cliente, porque el QR tiene que existir antes de hablar con
    # la base de datos. Solo caracteres seguros: viaja en URLs y en el QR.
    intent_reference: str = Field(min_length=4, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    terminal_id: str | None = Field(default=None, max_length=8)
    amount_minor: int = Field(gt=0, le=99_999_999)
    currency_code: Literal["PEN", "USD", "EUR"] = "PEN"
    line_items: list[LineItem] = Field(default_factory=list)
    statement_description: str | None = Field(default=None, max_length=48)
    # Online: adonde vuelve el comprador tras autenticarse con su banco.
    return_url: str | None = Field(default=None, max_length=500)

    @field_validator("return_url")
    @classmethod
    def return_url_must_be_https(cls, url: str | None) -> str | None:
        """
        HTTPS, o localhost para desarrollo.

        Es la URL a la que se redirige al comprador: si admitiera cualquier
        esquema, `javascript:` incluido, seria una puerta abierta.
        """
        if url is None:
            return url
        if url.startswith("https://") or url.startswith(("http://localhost", "http://127.0.0.1")):
            return url
        raise ValueError("return_url tiene que ser https://")

    @field_validator("line_items")
    @classmethod
    def lines_must_match_amount(cls, lines: list[LineItem], info) -> list[LineItem]:
        """
        Si vienen lineas, tienen que sumar el importe pedido.

        Un descuadre aqui es un bug del POS, y es mejor rechazarlo que cobrar un
        importe que no corresponde a lo que el cliente vio en pantalla.
        """
        amount = info.data.get("amount_minor")
        if lines and amount is not None:
            total = sum(line.quantity * line.unit_price_minor for line in lines)
            if total != amount:
                raise ValueError(
                    f"las lineas suman {total} pero amount_minor es {amount}"
                )
        return lines


class ConfirmCardRequest(BaseModel):
    """DEMO: el CVV es de prueba y la tarjeta es ficticia."""

    channel: Literal["CARD_PRESENT"] = "CARD_PRESENT"
    card_token: str = Field(max_length=64)
    # 3 o 4 digitos. Nunca se almacena ni se registra en el log.
    cvv: str = Field(min_length=3, max_length=4, pattern=r"^\d{3,4}$")


class ConfirmQrRequest(BaseModel):
    """La web de pago confirma. No hay credencial: es un mock de demo."""

    channel: Literal["QR"] = "QR"


class ConfirmOnlineRequest(BaseModel):
    """
    Una tienda online confirma con un PaymentMethod de prueba.

    Sin numero de tarjeta ni CVV: el token es el instrumento, como en las
    pruebas de la API de Stripe (pm_card_visa, pm_card_threeDSecure2Required...).
    """

    channel: Literal["CARD_NOT_PRESENT"] = "CARD_NOT_PRESENT"
    payment_method: str = Field(max_length=64, pattern=r"^pm_[A-Za-z0-9_]+$")


class CancelRequest(BaseModel):
    """Los motivos de cancelacion de Stripe."""

    cancellation_reason: Literal[
        "abandoned", "requested_by_customer", "duplicate", "fraudulent"
    ] = "requested_by_customer"


class AuthenticationResult(BaseModel):
    """Lo que devuelve la pagina del banco simulada."""

    token: str = Field(min_length=8, max_length=64)
    authenticated: bool


class IntentResponse(BaseModel):
    intent_reference: str
    status: str
    amount_minor: int
    amount_received_minor: int
    currency_code: str
    merchant_name: str
    line_items: list[LineItem]
    created_at: datetime
    channel: str | None = None
    # Por que fallo el ultimo intento: un DE 39 (05, 51...) o AUTH_3DS.
    last_error_code: str | None = None


class ChargeResponse(BaseModel):
    """
    El resultado de la autorizacion.

    Incluye los data elements de ISO 8583 porque son lo que el comercio necesita
    para reclamar: el RRN es la referencia que se busca en un contracargo.
    """

    intent_reference: str
    charge_reference: str | None = None
    # CAPTURED o FAILED si hubo autorizacion; REQUIRES_ACTION si el emisor pide
    # autenticar antes, y entonces no hay cobro todavia.
    status: str
    channel: str | None = None
    # DE 39: 00 aprobada, 05 no honrar. Vacio mientras no haya autorizacion.
    response_code: str | None = None
    # DE 38
    auth_code: str | None = None
    # DE 37 — la referencia para reclamos
    rrn: str | None = None
    # DE 11
    stan: str | None = None
    amount_minor: int
    currency_code: str
    # Lo que se le liquidara al comercio, ya descontado el MDR
    fee_minor: int | None = None
    net_minor: int | None = None
    decline_reason: str | None = None
    # Online con 3DS: a donde mandar al comprador.
    next_action: dict | None = None
    return_url: str | None = None


class HealthResponse(BaseModel):
    status: str
    database: str
    iot: str
