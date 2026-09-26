#!/usr/bin/env python3
"""
generate_dataset.py — genera el dataset sintetico de FinCorp.

Los CSV del repositorio estan commiteados y **no hace falta regenerarlos** para
seguir el workshop. Este script existe por tres razones:

  1. Hacer auditable la calibracion. Los problemas del dataset no son
     accidentes: son cantidades exactas, elegidas para que cada regla del
     ruleset DQDL del Lab 2 se comporte de una forma concreta y explicable.
     Sin generador, esos numeros son magia; con generador, son codigo.

  2. Permitir variantes. Si quieres un dataset de 100.000 filas para ver el
     pipeline con volumen, o cambiar el porcentaje de nulos para que la regla
     de completeness falle en vez de pasar, se hace aqui.

  3. Reproducibilidad. Con la misma semilla, dos ejecuciones dan ficheros
     identicos byte a byte.

Uso:
    python generate_dataset.py --check            # ver la calibracion sin escribir
    python generate_dataset.py --rows 100000      # variante con volumen
    python generate_dataset.py                    # SOBREESCRIBE los CSV del repo

IMPORTANTE: este script NO reproduce byte a byte los CSV commiteados. Genera un
dataset *equivalentemente calibrado*, con los mismos porcentajes de cada
problema, pero filas distintas. Los CSV del repositorio son la referencia: son
los que miden los README del Lab 2 y los que comprueba tests/test_glue_job.py.

Si ejecutas este script sin --check, los CSV cambian y con ellos el numero exacto
de filas rechazadas (100 en el dataset de referencia). Los tests fallaran, y eso
es lo que se espera de ellos: avisan de que los numeros documentados ya no
cuadran. Si era intencionado, actualiza los tests y los README.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import unicodedata
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── Calibracion ──────────────────────────────────────────────────────────────
# Cada fraccion esta elegida para provocar un resultado concreto en el Lab 2.
# La columna "efecto" es lo que el participante ve en el quality report.
#
#   problema                fraccion  efecto en el Lab 2
#   ----------------------  --------  ----------------------------------------
#   amount vacio              5.0%    Completeness = 0.9500 justo en el umbral
#                                     (>= 0.95): PASA por un pelo. Con un nulo
#                                     mas fallaria. Ensena que un umbral es una
#                                     decision, no una verdad.
#   transaction_id duplicado  2.7%    Uniqueness FALLA y no hay forma de
#                                     arreglarlo transformando: se rechaza.
#   tipo en espanol           2.0%    ColumnValues FALLA en raw y la
#                                     transformacion lo RESUELVE (DEPOSITO ->
#                                     DEPOSIT). El dato era recuperable.
#   status con case raro      8.0%    Igual que el anterior, via .upper().
#   fecha en DD/MM/YYYY      10.0%    No es una regla DQDL: lo arregla la
#                                     transformacion y se ve en transform_stats.
FRACTION_NULL_AMOUNT = 0.050
FRACTION_DUPLICATE_ID = 0.027
FRACTION_SPANISH_TYPE = 0.020
FRACTION_WEIRD_STATUS = 0.080
FRACTION_NON_ISO_DATE = 0.100

DEFAULT_ROWS = 1000
DEFAULT_CUSTOMERS = 200
SEED = 2026

CURRENCIES = ["USD", "USD", "USD", "USD", "USD", "PEN", "PEN", "PEN", "EUR", "EUR"]
TYPES = ["DEPOSIT", "WITHDRAWAL", "TRANSFER"]
STATUSES = ["COMPLETED", "COMPLETED", "COMPLETED", "PENDING", "FAILED", "PROCESSING"]

# Los tipos en espanol que trae el sistema origen: el caso clasico de dos
# equipos que nombraron lo mismo de forma distinta.
SPANISH_TYPES = {"DEPOSIT": "DEPÓSITO", "WITHDRAWAL": "RETIRO", "TRANSFER": "TRANSFERENCIA"}
WEIRD_STATUSES = ["pending", "Pending", "completed", "Completed", "failed", "processing"]

DATE_START = date(2024, 1, 1)
DATE_END = date(2026, 9, 19)

FIRST_NAMES = [
    "Roberto", "María", "Carlos", "Ana", "Luis", "Carmen", "Jorge", "Rosa",
    "Miguel", "Patricia", "José", "Elena", "Fernando", "Lucía", "Ricardo",
    "Sofía", "Andrés", "Valeria", "Diego", "Gabriela",
]
LAST_NAMES = [
    "García", "Rodríguez", "Quispe", "Flores", "Vargas", "Mendoza", "Torres",
    "Ramírez", "Castillo", "Rojas", "Sánchez", "Chávez", "Huamán", "Paredes",
    "Salazar", "Espinoza", "Cáceres", "Ibáñez", "Navarro", "Zamora",
]
EMAIL_DOMAINS = ["gmail.com", "hotmail.com", "outlook.com", "yahoo.com", "fincorp.pe"]
COUNTRIES = ["PE", "PE", "PE", "PE", "CL", "CO", "MX", "EC", "AR", "BR"]
RISK_LEVELS = ["LOW", "LOW", "LOW", "MEDIUM", "MEDIUM", "HIGH"]


def _slug(text: str) -> str:
    """Quita acentos para construir el email a partir del nombre."""
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def random_date(rng: random.Random, start: date = DATE_START, end: date = DATE_END) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def generate_customers(rng: random.Random, n: int) -> list[dict]:
    """Clientes limpios a proposito: el Lab 2 solo tiene que pelearse con las transacciones."""
    customers = []
    for i in range(1, n + 1):
        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        customers.append({
            "customer_id": f"CUS{i:03d}",
            "name": f"{first} {last}",
            "email": f"{_slug(first)}.{_slug(last)}{i}@{rng.choice(EMAIL_DOMAINS)}",
            "country": rng.choice(COUNTRIES),
            "risk_level": rng.choice(RISK_LEVELS),
            "onboarding_date": random_date(rng, date(2020, 1, 1), date(2026, 6, 30)).isoformat(),
        })
    return customers


def generate_transactions(rng: random.Random, n: int, customers: list[dict]) -> list[dict]:
    """Transacciones limpias primero; los problemas se inyectan despues, a proposito."""
    customer_ids = [c["customer_id"] for c in customers]
    rows = []
    for i in range(1, n + 1):
        rows.append({
            "transaction_id": f"TXN{i:07d}",
            "customer_id": rng.choice(customer_ids),
            "amount": f"{rng.uniform(50, 50000):.2f}",
            "currency": rng.choice(CURRENCIES),
            "transaction_type": rng.choice(TYPES),
            "status": rng.choice(STATUSES),
            "transaction_date": random_date(rng).isoformat(),
            "branch_id": f"BR{rng.randrange(1, 21):03d}",
        })
    return rows


def inject_problems(rng: random.Random, rows: list[dict]) -> dict:
    """
    Mete los problemas deliberados y devuelve cuantos de cada uno.

    Los indices se eligen sin solaparse por problema, para que cada regla del
    Lab 2 tenga un numero predecible. Una fila si puede acumular dos problemas
    distintos: eso es realista y hace que rejection_reason muestre varios
    motivos a la vez.
    """
    n = len(rows)
    stats = {}

    def pick(fraction: float) -> list[int]:
        return rng.sample(range(n), int(round(n * fraction)))

    # amount vacio: el sistema origen permitia guardar transacciones sin importe
    for i in pick(FRACTION_NULL_AMOUNT):
        rows[i]["amount"] = ""
    stats["null_amount"] = int(round(n * FRACTION_NULL_AMOUNT))

    # tipo en espanol: dos equipos, dos vocabularios
    spanish = pick(FRACTION_SPANISH_TYPE)
    for i in spanish:
        rows[i]["transaction_type"] = SPANISH_TYPES[
            rows[i]["transaction_type"]
            if rows[i]["transaction_type"] in SPANISH_TYPES
            else "DEPOSIT"
        ]
    stats["spanish_type"] = len(spanish)

    # status sin normalizar: distintas versiones de la app escribiendo distinto
    weird = pick(FRACTION_WEIRD_STATUS)
    for i in weird:
        rows[i]["status"] = rng.choice(WEIRD_STATUSES)
    stats["weird_status"] = len(weird)

    # fecha en DD/MM/YYYY: locale distinto en un servidor
    non_iso = pick(FRACTION_NON_ISO_DATE)
    for i in non_iso:
        y, m, d = rows[i]["transaction_date"].split("-")
        rows[i]["transaction_date"] = f"{d}/{m}/{y}"
    stats["non_iso_date"] = len(non_iso)

    # transaction_id duplicado: reintentos de un proceso de carga sin idempotencia.
    # Se copia el id de otra fila, de modo que quedan 2 filas con el mismo id.
    dup_targets = rng.sample(range(n), int(round(n * FRACTION_DUPLICATE_ID)))
    for i in dup_targets:
        source = rng.choice([j for j in range(n) if j != i])
        rows[i]["transaction_id"] = rows[source]["transaction_id"]
    stats["duplicate_id_rows"] = len(dup_targets)

    return stats


def write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def summarize(rows: list[dict]) -> dict:
    """Mide el resultado, para poder comprobar la calibracion."""
    import collections

    ids = collections.Counter(r["transaction_id"] for r in rows)
    return {
        "rows": len(rows),
        "null_amount": sum(1 for r in rows if not r["amount"]),
        "duplicate_id_rows": sum(c for c in ids.values() if c > 1),
        "non_canonical_type": sum(1 for r in rows if r["transaction_type"] not in TYPES),
        "non_canonical_status": sum(
            1 for r in rows if r["status"] not in {"PENDING", "COMPLETED", "FAILED", "PROCESSING"}
        ),
        "non_iso_date": sum(1 for r in rows if "/" in r["transaction_date"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS, help=f"transacciones (default {DEFAULT_ROWS})")
    parser.add_argument("--customers", type=int, default=DEFAULT_CUSTOMERS, help=f"clientes (default {DEFAULT_CUSTOMERS})")
    parser.add_argument("--seed", type=int, default=SEED, help=f"semilla (default {SEED})")
    parser.add_argument("--out", type=Path, default=HERE, help="directorio de salida")
    parser.add_argument("--check", action="store_true", help="no escribe: solo imprime la calibracion")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    customers = generate_customers(rng, args.customers)
    transactions = generate_transactions(rng, args.rows, customers)
    injected = inject_problems(rng, transactions)
    measured = summarize(transactions)

    print(f"Semilla {args.seed} · {args.rows} transacciones · {args.customers} clientes\n")
    print("Problemas inyectados y medidos:")
    for key in ("null_amount", "duplicate_id_rows", "non_iso_date"):
        print(f"  {key:22s} inyectados={injected.get(key, '-'):>5} medidos={measured[key]:>5}")
    print(f"  {'non_canonical_type':22s} inyectados={injected['spanish_type']:>5} medidos={measured['non_canonical_type']:>5}")
    print(f"  {'non_canonical_status':22s} inyectados={injected['weird_status']:>5} medidos={measured['non_canonical_status']:>5}")

    completeness = 1 - measured["null_amount"] / measured["rows"]
    print(f"\nCompleteness de amount: {completeness:.4f} (umbral del Lab 2: >= 0.95)")
    print(f"  -> la regla {'PASA' if completeness >= 0.95 else 'FALLA'}", end="")
    if abs(completeness - 0.95) < 0.001:
        print(" justo en el borde: con un nulo mas fallaria")
    else:
        print()

    if args.check:
        print("\n--check: no se ha escrito nada.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "transactions.csv", transactions)
    write_csv(args.out / "customers.csv", customers)
    print(f"\nEscrito en {args.out}/")
    print("  transactions.csv")
    print("  customers.csv")
    print("\nSi cambiaste las cantidades, ejecuta 'pytest tests/' — los tests")
    print("fijan los numeros que citan los README del Lab 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
