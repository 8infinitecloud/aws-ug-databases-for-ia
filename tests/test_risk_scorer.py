"""
Pruebas del scoring de riesgo del Lab 4.

Las reglas son funciones puras, así que se prueban sin DynamoDB, sin Lambda y
sin AWS. Eso no es una comodidad: es la razón de que estén separadas de la E/S.
Una regla de fraude se itera decenas de veces hasta calibrarla, y hacerlo contra
un servicio desplegado es inviable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORER = REPO_ROOT / "labs/04-streaming-online-insight/risk_scorer.py"


def _cargar():
    spec = importlib.util.spec_from_file_location("risk_scorer", SCORER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["risk_scorer"] = mod
    spec.loader.exec_module(mod)
    return mod


rs = _cargar()


def evento(**kw) -> dict:
    """Un evento como el que publica el backend de payments."""
    base = {
        "event_type": "payment.captured",
        "event_version": "1.0",
        "transaction_id": "ch_PI0001_1",
        "merchant_id": "MID000000012345",
        "mcc": "5812",
        "risk_level": "MEDIUM",
        "pos_id": "TID00001",
        "device_id": "012370911fd7211101",
        "payment_method": "CARD_PRESENT",
        "amount": 18.50,
        "currency": "PEN",
        "status": "CAPTURED",
        "response_code": "00",
        "timestamp": "2026-09-28T10:00:05+00:00",
    }
    base.update(kw)
    return base


def contadores(**kw) -> "rs.Counters":
    return rs.Counters(**{"transactions": 1, "amount": 18.50, "declines": 0, **kw})


# ── Una operación normal no molesta a nadie ──────────────────────────────────
def test_una_venta_corriente_no_levanta_ninguna_alerta():
    decision = rs.score(evento(), contadores())
    assert decision.flagged is False
    assert decision.alerts == []


# ── Velocidad por número de operaciones ──────────────────────────────────────
def test_el_umbral_de_velocidad_no_dispara_justo_en_el_limite():
    """
    En el umbral exacto no salta.

    Un `>=` aquí marcaría la quinta venta legítima de la mañana, y una regla que
    marca operaciones normales se acaba desactivando entera.
    """
    c = contadores(transactions=rs.MAX_TX_PER_WINDOW)
    assert rs.rule_velocity_count(evento(), c) is None


def test_pasado_el_umbral_salta_como_alta():
    c = contadores(transactions=rs.MAX_TX_PER_WINDOW + 1)
    alerta = rs.rule_velocity_count(evento(), c)

    assert alerta is not None
    assert alerta.rule == "VELOCITY_COUNT"
    assert alerta.severity == "HIGH"
    assert alerta.observed == rs.MAX_TX_PER_WINDOW + 1


# ── Velocidad por monto ──────────────────────────────────────────────────────
def test_el_monto_acumulado_dispara_por_encima_del_umbral():
    c = contadores(amount=rs.MAX_AMOUNT_PER_WINDOW + 0.01)
    alerta = rs.rule_velocity_amount(evento(), c)

    assert alerta is not None
    assert alerta.rule == "VELOCITY_AMOUNT"


def test_el_monto_en_el_umbral_no_dispara():
    c = contadores(amount=rs.MAX_AMOUNT_PER_WINDOW)
    assert rs.rule_velocity_amount(evento(), c) is None


# ── Proporción de declinaciones: la firma del card testing ───────────────────
def test_una_declinacion_aislada_no_dispara_el_ratio():
    """
    Con pocas muestras el ratio no significa nada.

    Dos declinaciones de tres operaciones dan el 67%, que sí supera el umbral, y
    aun así no se marca: por debajo del mínimo de muestras marcaríamos a
    cualquiera que tenga un par de rechazos temprano.
    """
    c = contadores(transactions=3, declines=2)
    assert c.transactions < rs.MIN_SAMPLE_FOR_RATIO
    assert c.decline_ratio > rs.MAX_DECLINE_RATIO   # el ratio sí lo supera
    assert rs.rule_decline_ratio(evento(), c) is None


def test_una_racha_de_declinaciones_si_dispara():
    c = contadores(transactions=8, declines=6)
    alerta = rs.rule_decline_ratio(evento(), c)

    assert alerta is not None
    assert alerta.rule == "DECLINE_RATIO"
    assert alerta.severity == "HIGH"
    assert "6/8" in alerta.message


def test_el_ratio_de_declinaciones_se_calcula_sin_dividir_por_cero():
    assert rs.Counters().decline_ratio == 0.0


# ── MCC de riesgo ────────────────────────────────────────────────────────────
def test_un_importe_alto_en_riesgo_bajo_no_es_alerta():
    """Es la combinación lo que importa, no el importe solo."""
    e = evento(risk_level="LOW", amount=rs.HIGH_RISK_AMOUNT + 100)
    assert rs.rule_high_risk_mcc(e, contadores()) is None


def test_un_importe_alto_en_riesgo_alto_si_lo_es():
    e = evento(risk_level="HIGH", mcc="6051", amount=rs.HIGH_RISK_AMOUNT + 0.01)
    alerta = rs.rule_high_risk_mcc(e, contadores())

    assert alerta is not None
    assert alerta.rule == "HIGH_RISK_MCC"
    assert "6051" in alerta.message


# ── La ventana ───────────────────────────────────────────────────────────────
def test_dos_eventos_del_mismo_minuto_caen_en_la_misma_ventana():
    a = rs.window_key("2026-09-28T10:00:05+00:00")
    b = rs.window_key("2026-09-28T10:00:59+00:00")
    assert a == b


def test_el_minuto_siguiente_es_otra_ventana():
    a = rs.window_key("2026-09-28T10:00:59+00:00")
    b = rs.window_key("2026-09-28T10:01:00+00:00")
    assert a != b


def test_un_timestamp_ilegible_no_rompe_el_scoring():
    """Un evento con fecha corrupta se cuenta en la ventana actual, no se pierde."""
    assert rs.window_key("no-es-una-fecha").isdigit()


def test_el_formato_z_de_utc_se_entiende():
    """El POS puede mandar 'Z' en vez de '+00:00'."""
    assert rs.window_key("2026-09-28T10:00:05Z") == rs.window_key("2026-09-28T10:00:05+00:00")


# ── La clave de partición ────────────────────────────────────────────────────
def test_los_contadores_van_por_terminal():
    """
    Un comercio con diez cajas mueve diez veces más que uno con una.

    Agregando por comercio, el mismo umbral significaría cosas distintas según
    el tamaño del local.
    """
    assert rs.partition_key(evento(pos_id="TID00001")) == "T#TID00001"
    assert rs.partition_key(evento(pos_id="TID00002")) != rs.partition_key(evento(pos_id="TID00001"))


def test_sin_terminal_se_cae_al_comercio():
    e = evento(pos_id=None)
    assert rs.partition_key(e) == "T#MID000000012345"


# ── El conjunto ──────────────────────────────────────────────────────────────
def test_una_rafaga_de_card_testing_levanta_varias_reglas():
    """El caso que el lab enseña: muchas operaciones y casi todas rechazadas."""
    e = evento(status="FAILED", response_code="05")
    c = contadores(transactions=20, amount=180.0, declines=18)

    decision = rs.score(e, c)
    reglas = {a.rule for a in decision.alerts}

    assert decision.flagged is True
    assert "VELOCITY_COUNT" in reglas
    assert "DECLINE_RATIO" in reglas


def test_la_decision_lleva_lo_que_el_dashboard_necesita():
    decision = rs.score(evento(), contadores(transactions=99, declines=99))

    assert decision.transaction_id == "ch_PI0001_1"
    assert decision.merchant_id == "MID000000012345"
    assert decision.terminal_id == "TID00001"
    assert decision.scored_at.endswith("+00:00")


@pytest.mark.parametrize("codigo,es_declinacion", [("00", False), ("05", True), ("51", True)])
def test_solo_el_00_de_iso8583_cuenta_como_aprobada(codigo, es_declinacion):
    assert (codigo != rs.RESPONSE_CODE_APPROVED) is es_declinacion
