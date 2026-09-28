#!/usr/bin/env python3
"""
run_streaming_local.py — el Lab 4 entero sin AWS.

Ejecuta el **mismo** `lambda_handler` que corre en Lambda, contra un doble de
DynamoDB en memoria. Sirve para:

  - Ver el card testing detectarse, que es lo que el lab enseña
  - Calibrar los umbrales sin desplegar ni esperar a CloudWatch
  - Entender el Lab 4 antes de tener una cuenta AWS

No es una reimplementacion: se sustituye la tabla por un doble que implementa
los dos metodos que usa el scorer (update_item con ADD, y put_item).

Uso:
    python scripts/run_streaming_local.py
    python scripts/run_streaming_local.py --escenario cafeteria
    python scripts/run_streaming_local.py --escenario card-testing
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCORER = REPO_ROOT / "labs/04-streaming-online-insight/risk_scorer.py"

VERDE, ROJO, AMARILLO, AZUL, TENUE, NC = (
    "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0;34m", "\033[2m", "\033[0m"
)


class TablaEnMemoria:
    """
    El doble de DynamoDB.

    Solo implementa lo que usa el scorer. `update_item` con ADD es lo unico con
    algo de logica: suma sobre lo que haya, que es justo lo que hace DynamoDB y
    lo que evita perder eventos simultaneos.
    """

    def __init__(self):
        self.items: dict[tuple, dict] = {}
        self.alertas: list[dict] = []

    def update_item(self, Key, UpdateExpression, ExpressionAttributeValues,
                    ReturnValues=None):  # noqa: N803 (firma de boto3)
        clave = (Key["pk"], Key["sk"])
        item = self.items.setdefault(clave, {**Key, "tx_count": 0,
                                             "amount_total": Decimal("0"), "declines": 0})
        v = ExpressionAttributeValues
        item["tx_count"] += v[":uno"]
        item["amount_total"] += v[":monto"]
        item["declines"] += v[":dec"]
        item["expires_at"] = v[":ttl"]
        return {"Attributes": item}

    def put_item(self, Item):  # noqa: N803
        self.items[(Item["pk"], Item["sk"])] = Item
        self.alertas.append(Item)
        return {}


def cargar_scorer():
    spec = importlib.util.spec_from_file_location("risk_scorer", SCORER)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["risk_scorer"] = mod
    spec.loader.exec_module(mod)
    return mod


def evento(n, *, amount, aprobada=True, segundos=0, terminal="TID00001",
           mcc="5812", risk="MEDIUM"):
    inicio = datetime(2026, 9, 28, 10, 0, 0, tzinfo=timezone.utc)
    return {
        "event_type": "payment.captured" if aprobada else "payment.declined",
        "event_version": "1.0",
        "transaction_id": f"ch_PI{n:04d}_1",
        "merchant_id": "MID000000012345",
        "mcc": mcc,
        "risk_level": risk,
        "pos_id": terminal,
        "device_id": "012370911fd7211101",
        "payment_method": "CARD_PRESENT",
        "amount": amount,
        "currency": "PEN",
        "status": "CAPTURED" if aprobada else "FAILED",
        "response_code": "00" if aprobada else "05",
        "timestamp": (inicio + timedelta(seconds=segundos)).isoformat(),
    }


ESCENARIOS = {
    "cafeteria": (
        "Una manana normal: ventas espaciadas, todas aprobadas",
        [evento(i, amount=a, segundos=i * 90)
         for i, a in enumerate([8.00, 18.50, 12.00, 8.00, 24.50, 18.50], start=1)],
    ),
    "card-testing": (
        "Alguien prueba tarjetas robadas: rafaga en el mismo minuto, casi todas rechazadas",
        [evento(i, amount=1.00, aprobada=(i % 7 == 0), segundos=i * 3)
         for i in range(1, 15)],
    ),
    "casa-de-cambio": (
        "Importe alto en un comercio de riesgo alto (MCC 6051)",
        [evento(1, amount=450.00, mcc="6051", risk="HIGH", terminal="TID00099")],
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--escenario", choices=[*ESCENARIOS, "todos"], default="todos")
    args = parser.parse_args()

    os.environ.setdefault("VELOCITY_TABLE", "tabla-local")
    rs = cargar_scorer()

    nombres = list(ESCENARIOS) if args.escenario == "todos" else [args.escenario]

    print(f"\n{AZUL}{'=' * 72}{NC}")
    print(f"{AZUL}  Lab 4 en local — scoring de riesgo, sin AWS{NC}")
    print(f"{AZUL}{'=' * 72}{NC}")
    print(f"  {TENUE}ventana {rs.WINDOW_SECONDS}s · max {rs.MAX_TX_PER_WINDOW} tx · "
          f"max {rs.MAX_AMOUNT_PER_WINDOW:.0f} · ratio declinaciones {rs.MAX_DECLINE_RATIO:.0%}{NC}")

    total_marcadas = 0

    for nombre in nombres:
        descripcion, eventos = ESCENARIOS[nombre]
        tabla = TablaEnMemoria()
        # El handler crea su propia tabla: se sustituye por el doble.
        rs._table = lambda t=tabla: t

        print(f"\n{AZUL}── {nombre} {'─' * (66 - len(nombre))}{NC}")
        print(f"  {TENUE}{descripcion}{NC}\n")

        for e in eventos:
            contadores = rs.bump_counters(tabla, e)
            decision = rs.score(e, contadores)
            if decision.flagged:
                rs.save_alert(tabla, decision, e)
                total_marcadas += 1

            marca = f"{ROJO}MARCADA{NC}" if decision.flagged else f"{VERDE}   ok  {NC}"
            reglas = ", ".join(a.rule for a in decision.alerts)
            hora = e["timestamp"][11:19]
            estado = "OK " if e["response_code"] == "00" else "DEC"
            print(f"  {hora}  {estado}  {e['amount']:7.2f}  {marca}  "
                  f"{TENUE}{contadores.transactions:2d}tx/{contadores.amount:7.2f}{NC}  {AMARILLO}{reglas}{NC}")

        if tabla.alertas:
            print(f"\n  {ROJO}{len(tabla.alertas)} operaciones marcadas.{NC} "
                  f"{TENUE}En AWS quedarian en DynamoDB para el dashboard.{NC}")
        else:
            print(f"\n  {VERDE}Ninguna alerta.{NC} {TENUE}Es el comportamiento esperado aqui.{NC}")

    print(f"\n{AZUL}{'=' * 72}{NC}")
    print(f"  {total_marcadas} marcadas en total. "
          f"{TENUE}Ajusta los umbrales con variables de entorno:{NC}")
    print(f"    {TENUE}MAX_TX_PER_WINDOW=3 python scripts/run_streaming_local.py --escenario cafeteria{NC}")
    print(f"{AZUL}{'=' * 72}{NC}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
