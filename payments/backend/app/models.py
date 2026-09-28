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
    """El POS cierra el carrito y pide una intencion de cobro."""

    # La genera el POS, porque el QR tiene que existir antes de hablar con la
    # base de datos.
    intent_reference: str = Field(min_length=4, max_length=40)
    terminal_id: str = Field(max_length=8)
    amount_minor: int = Field(gt=0, le=99_999_999)
    currency_code: Literal["PEN", "USD", "EUR"] = "PEN"
    line_items: list[LineItem] = Field(default_factory=list)
    statement_description: str | None = Field(default=None, max_length=48)

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


class IntentResponse(BaseModel):
    intent_reference: str
    status: str
    amount_minor: int
    amount_received_minor: int
    currency_code: str
    merchant_name: str
    line_items: list[LineItem]
    created_at: datetime


class ChargeResponse(BaseModel):
    """
    El resultado de la autorizacion.

    Incluye los data elements de ISO 8583 porque son lo que el comercio necesita
    para reclamar: el RRN es la referencia que se busca en un contracargo.
    """

    intent_reference: str
    charge_reference: str | None = None
    status: str
    # DE 39: 00 aprobada, 05 no honrar
    response_code: str
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


class HealthResponse(BaseModel):
    status: str
    database: str
    iot: str
