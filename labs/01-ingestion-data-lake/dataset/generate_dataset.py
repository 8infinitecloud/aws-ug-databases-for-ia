#!/usr/bin/env python3
"""
generate_dataset.py — dataset historico de adquirencia de FinCorp Payments.

Los CSV del repositorio estan commiteados y **no hace falta regenerarlos** para
seguir el workshop. Este script existe por tres razones:

  1. Hacer auditable la calibracion. Los problemas del dataset no son
     accidentes: son cantidades exactas, elegidas para que cada regla del
     ruleset DQDL del Lab 2 se comporte de una forma concreta y explicable.

  2. Permitir variantes. Un dataset de 500.000 filas para ver donde se queda
     corto Glue Python Shell, o cambiar un porcentaje para que una regla falle
     en vez de pasar.

  3. Reproducibilidad. Con la misma semilla, dos ejecuciones dan ficheros
     identicos byte a byte.

EL CONTRATO ES LA VISTA
Las columnas de transactions.csv son EXACTAMENTE las de
`acquiring.v_transactions_for_ingestion` (ver payments/schema/001_schema.sql).
Eso no es casualidad: es lo que DMS replica al data lake, asi que el dataset
historico y el flujo en tiempo real tienen la misma forma y el Lab 2 no tiene que
distinguirlos.

POSICION: somos el ADQUIRENTE, no el comercio. Estas no son ventas de cafe: son
autorizaciones de pago que procesamos para nuestros comercios afiliados. De ahi
que las filas tengan MID, MCC, terminal, comision y neto.

Uso:
    python generate_dataset.py --check            # ver la calibracion sin escribir
    python generate_dataset.py --rows 500000      # variante con volumen
    python generate_dataset.py                    # SOBREESCRIBE los CSV del repo

IMPORTANTE: este script NO reproduce byte a byte los CSV commiteados. Genera un
dataset *equivalentemente calibrado*, con los mismos porcentajes de cada problema
pero filas distintas. Los CSV del repositorio son la referencia: son los que miden
los README del Lab 2 y los que comprueba tests/test_glue_job.py.
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent

# ── Calibracion ──────────────────────────────────────────────────────────────
# Cada fraccion provoca un resultado concreto en el Lab 2. La columna "efecto" es
# lo que el participante ve en el quality report.
#
# Los tres primeros son problemas de FORMA (falta un dato, viene mal escrito).
# Los dos ultimos son propios de la adquirencia y mas interesantes: son
# violaciones de REGLA DE NEGOCIO que ninguna validacion de columna detecta.
#
#   problema                    fraccion  efecto en el Lab 2
#   --------------------------  --------  --------------------------------------
#   amount vacio                  5.0%    Completeness = 0.9500 justo en el
#                                         umbral (>= 0.95): PASA por un pelo.
#                                         Con un nulo mas fallaria.
#   transaction_id duplicado      2.7%    Uniqueness FALLA y no se arregla
#                                         transformando: se rechaza.
#   payment_method heredado       2.0%    El core antiguo escribia TARJETA y POS.
#                                         Falla en raw y lo RESUELVE la
#                                         transformacion: el dato era recuperable.
#   status con case raro          8.0%    Igual, via .upper().
#   fecha en DD/MM/YYYY          10.0%    No es regla DQDL: lo arregla la
#                                         transformacion y sale en transform_stats.
#   libro descuadrado             3.0%    net != amount - fee. NINGUNA validacion
#                                         de columna lo ve: hay que comparar tres
#                                         campos. Es el fallo que hace que una
#                                         liquidacion salga mal.
#   aprobada sin codigo           1.5%    status CAPTURED con response_code != 00.
#                                         Contradiccion interna: la operacion dice
#                                         estar cobrada y el emisor dice que no.
FRACTION_NULL_AMOUNT = 0.050
FRACTION_DUPLICATE_ID = 0.027
FRACTION_LEGACY_METHOD = 0.020
FRACTION_WEIRD_STATUS = 0.080
FRACTION_NON_ISO_DATE = 0.100
FRACTION_LEDGER_MISMATCH = 0.030
FRACTION_APPROVED_NO_AUTH = 0.015

DEFAULT_ROWS = 1000
DEFAULT_MERCHANTS = 200
SEED = 2026

# ── Dominio de adquirencia ───────────────────────────────────────────────────
CURRENCIES = ["PEN"] * 6 + ["USD"] * 3 + ["EUR"]
PAYMENT_METHODS = ["CARD_PRESENT"] * 7 + ["QR"] * 3
STATUSES = ["CAPTURED"] * 8 + ["FAILED", "REFUNDED"]

# Codigos que escribia el core antiguo antes de normalizar el catalogo. El caso
# clasico de dos generaciones de sistema nombrando lo mismo distinto.
LEGACY_METHODS = {"CARD_PRESENT": "TARJETA", "QR": "CODIGO_QR"}
WEIRD_STATUSES = ["captured", "Captured", "failed", "Failed", "refunded", "CAPTURADA"]

# ISO 8583 DE 39. 00 aprueba; el resto rechaza.
RESPONSE_CODES_DECLINED = ["05", "51", "54", "61", "91"]

# MCC (ISO 18245). Comercios variados, porque el MCC es en si una senal de riesgo
# y el monitoreo AML lo usa.
# El nombre comercial se deriva del MCC, no se elige aparte: una casa de cambio
# tiene que tener MCC 6051, no 5814. El MCC es una senal de riesgo AML por si
# mismo, asi que un comercio cuyo giro no cuadra con su MCC seria justo el tipo
# de incoherencia que despista al participante.
MCC_POOL = [
    # (mcc,  giro,                riesgo,   nombres plausibles para ese giro)
    ("5812", "Restaurantes",      "LOW",    ["Sabor Andino", "Don Pollo", "La Cocina", "Mi Menu"]),
    ("5814", "Comida rapida",     "LOW",    ["Smart Coffee", "Pollo Express", "Cafe Rapido", "Burger Punto"]),
    ("5411", "Supermercados",     "LOW",    ["Super Plaza", "Bodega Central", "Mi Market", "Minimarket Sur"]),
    ("5912", "Farmacias",         "LOW",    ["Farma Salud", "Botica Lima", "Farmacia Andina"]),
    ("5732", "Electronica",       "MEDIUM", ["Tech Punto", "Digital Store", "Electro Plaza"]),
    ("5691", "Ropa",              "MEDIUM", ["Moda Lima", "Casa Textil", "Boutique Andina"]),
    ("7011", "Hoteles",           "MEDIUM", ["Hotel Plaza", "Hostal Miraflores", "Casa Andina Inn"]),
    ("4722", "Agencias de viaje", "HIGH",   ["Viajes Peru", "Tour Andino", "Destinos SAC"]),
    ("6051", "Casas de cambio",   "HIGH",   ["El Cambio", "Cambios Lima", "Divisas Express"]),
    ("7995", "Apuestas",          "HIGH",   ["Juegos Lima", "Apuesta Ya", "Sala Central"]),
]
COUNTRIES = ["PE"] * 7 + ["CL", "CO", "EC"]

DATE_START = date(2024, 1, 1)
DATE_END = date(2026, 9, 19)

# El terminal de la demo: serial real del elemento seguro ATECC608 del M5Stack.
DEMO_DEVICE_SERIAL = "012370911fd7211101"
DEMO_MERCHANT_ID = "MID000000012345"


def _slug(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower()


def random_date(rng: random.Random, start: date = DATE_START, end: date = DATE_END) -> date:
    return start + timedelta(days=rng.randrange((end - start).days + 1))


def generate_merchants(rng: random.Random, n: int) -> list[dict]:
    """
    Los comercios afiliados. Son NUESTROS clientes.

    Limpios a proposito: el Lab 2 solo tiene que pelearse con las transacciones.
    El primero es siempre Smart Coffee, el del POS fisico de la demo.
    """
    merchants = []
    for i in range(1, n + 1):
        if i == 1:
            # Smart Coffee: comida rapida, MCC 5814. Es el comercio del POS fisico.
            mcc = "5814"
            merchants.append({
                "merchant_id": DEMO_MERCHANT_ID,
                "legal_name": "Smart Coffee Peru S.A.C.",
                "trade_name": "Smart Coffee",
                "mcc": mcc,
                "country_code": "PE",
                "risk_level": "MEDIUM",
                # MDR en puntos basicos: 2.99%
                "mdr_bps": 299,
                "fixed_fee_minor": 30,
                "status": "ACTIVE",
                "onboarding_date": "2026-06-29",
            })
            continue

        mcc, giro, risk_base, nombres = rng.choice(MCC_POOL)
        # Un sufijo numerico cuando hay mas comercios que nombres del giro: es
        # realista (cadenas con varias sucursales afiliadas por separado).
        base = rng.choice(nombres)
        trade = base if rng.random() < 0.5 else f"{base} {rng.randrange(2, 40)}"
        # El MDR sube con el riesgo: es como un adquirente cubre la exposicion.
        mdr = {"LOW": 249, "MEDIUM": 299, "HIGH": 389}[risk_base]
        merchants.append({
            "merchant_id": f"MID{i:012d}",
            "legal_name": f"{trade} {rng.choice(['S.A.C.', 'E.I.R.L.', 'S.R.L.'])}",
            "trade_name": trade,
            "mcc": mcc,
            "country_code": rng.choice(COUNTRIES),
            "risk_level": risk_base,
            "mdr_bps": mdr,
            "fixed_fee_minor": rng.choice([20, 30, 50]),
            "status": "ACTIVE" if rng.random() > 0.04 else "SUSPENDED",
            "onboarding_date": random_date(rng, date(2020, 1, 1), date(2026, 6, 30)).isoformat(),
        })
    return merchants


def generate_transactions(rng: random.Random, n: int, merchants: list[dict]) -> list[dict]:
    """
    Autorizaciones limpias. Los problemas se inyectan despues, a proposito.

    Las columnas son EXACTAMENTE las de v_transactions_for_ingestion.
    """
    by_id = {m["merchant_id"]: m for m in merchants}
    ids = list(by_id)
    rows = []

    for i in range(1, n + 1):
        merchant = by_id[rng.choice(ids)]
        method = rng.choice(PAYMENT_METHODS)
        status = rng.choice(STATUSES)
        amount_minor = rng.randrange(500, 5_000_00)  # S/5.00 a S/5000.00

        # La comision, con el MDR del comercio. Es la misma formula que aplica el
        # backend en acquiring.py, y por eso el dato historico y el de tiempo real
        # cuadran entre si.
        fee_minor = (amount_minor * merchant["mdr_bps"]) // 10_000 + merchant["fixed_fee_minor"]
        net_minor = amount_minor - fee_minor

        approved = status in ("CAPTURED", "REFUNDED")
        response = "00" if approved else rng.choice(RESPONSE_CODES_DECLINED)
        created = random_date(rng)
        # El RRN lleva la fecha: convencion de la industria, hace la referencia
        # reconocible al buscarla en una reclamacion.
        rrn = created.strftime("%y%m%d") + f"{rng.randrange(0, 999_999):06d}"

        # El terminal de la demo solo aparece en transacciones de Smart Coffee.
        if merchant["merchant_id"] == DEMO_MERCHANT_ID:
            terminal, serial = "TID00001", DEMO_DEVICE_SERIAL
        else:
            terminal, serial = f"TID{rng.randrange(1, 9999):05d}", ""

        rows.append({
            "transaction_id": f"ch_TX{i:07d}",
            "customer_id": merchant["merchant_id"],   # el comercio ES nuestro cliente
            "merchant_name": merchant["trade_name"],
            "mcc": merchant["mcc"],
            "risk_level": merchant["risk_level"],
            "terminal_id": terminal,
            "device_serial": serial,
            "amount": f"{amount_minor / 100:.2f}",
            "currency": rng.choice(CURRENCIES),
            "payment_method": method,
            "status": status,
            "response_code": response,
            "rrn": rrn,
            "auth_code": f"MOCK-{rng.randrange(1000, 9999)}" if approved else "",
            "fee_amount": f"{fee_minor / 100:.2f}" if approved else "",
            "net_amount": f"{net_minor / 100:.2f}" if approved else "",
            "transaction_date": created.isoformat(),
            "created_at": datetime.combine(
                created, datetime.min.time(), tzinfo=timezone.utc
            ).replace(hour=rng.randrange(7, 22), minute=rng.randrange(60)).isoformat(),
            "captured_at": "",
        })

    # captured_at solo tiene sentido si se capturo.
    for row in rows:
        if row["status"] in ("CAPTURED", "REFUNDED"):
            row["captured_at"] = row["created_at"]

    return rows


def inject_problems(rng: random.Random, rows: list[dict]) -> dict:
    """
    Mete los problemas deliberados y devuelve cuantos de cada uno.

    Una fila puede acumular varios: es realista y hace que rejection_reason
    muestre mas de un motivo a la vez.
    """
    n = len(rows)
    stats = {}

    def pick(fraction: float) -> list[int]:
        return rng.sample(range(n), int(round(n * fraction)))

    # ── Problemas de forma ────────────────────────────────────────────────────
    for i in pick(FRACTION_NULL_AMOUNT):
        rows[i]["amount"] = ""
    stats["null_amount"] = int(round(n * FRACTION_NULL_AMOUNT))

    legacy = pick(FRACTION_LEGACY_METHOD)
    for i in legacy:
        current = rows[i]["payment_method"]
        rows[i]["payment_method"] = LEGACY_METHODS.get(current, "TARJETA")
    stats["legacy_method"] = len(legacy)

    weird = pick(FRACTION_WEIRD_STATUS)
    for i in weird:
        rows[i]["status"] = rng.choice(WEIRD_STATUSES)
    stats["weird_status"] = len(weird)

    non_iso = pick(FRACTION_NON_ISO_DATE)
    for i in non_iso:
        y, m, d = rows[i]["transaction_date"].split("-")
        rows[i]["transaction_date"] = f"{d}/{m}/{y}"
    stats["non_iso_date"] = len(non_iso)

    # ── Problemas de regla de negocio: los propios de la adquirencia ─────────
    # El libro descuadrado. Se altera el neto sin tocar importe ni comision, que
    # es como se ve en la vida real: un proceso que recalculo mal.
    mismatch = [i for i in pick(FRACTION_LEDGER_MISMATCH) if rows[i]["net_amount"]]
    for i in mismatch:
        wrong = float(rows[i]["net_amount"]) * rng.choice([0.9, 1.1])
        rows[i]["net_amount"] = f"{wrong:.2f}"
    stats["ledger_mismatch"] = len(mismatch)

    # Aprobada sin aprobacion del emisor: la operacion dice estar cobrada y el
    # response_code dice que no. Contradiccion interna.
    contradictory = [i for i in pick(FRACTION_APPROVED_NO_AUTH) if rows[i]["status"] == "CAPTURED"]
    for i in contradictory:
        rows[i]["response_code"] = rng.choice(RESPONSE_CODES_DECLINED)
        rows[i]["auth_code"] = ""
    stats["approved_no_auth"] = len(contradictory)

    # El duplicado va al final para que no se pise con los demas.
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


VALID_METHODS = {"CARD_PRESENT", "QR"}
VALID_STATUSES = {"CAPTURED", "FAILED", "REFUNDED", "REVERSED"}


def summarize(rows: list[dict]) -> dict:
    import collections

    ids = collections.Counter(r["transaction_id"] for r in rows)

    def ledger_broken(r: dict) -> bool:
        if not (r["amount"] and r["fee_amount"] and r["net_amount"]):
            return False
        return abs(
            round(float(r["amount"]) - float(r["fee_amount"]) - float(r["net_amount"]), 2)
        ) > 0.01

    return {
        "rows": len(rows),
        "null_amount": sum(1 for r in rows if not r["amount"]),
        "duplicate_id_rows": sum(c for c in ids.values() if c > 1),
        "non_canonical_method": sum(1 for r in rows if r["payment_method"] not in VALID_METHODS),
        "non_canonical_status": sum(1 for r in rows if r["status"] not in VALID_STATUSES),
        "non_iso_date": sum(1 for r in rows if "/" in r["transaction_date"]),
        "ledger_mismatch": sum(1 for r in rows if ledger_broken(r)),
        "approved_no_auth": sum(
            1 for r in rows if r["status"] == "CAPTURED" and r["response_code"] != "00"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=DEFAULT_ROWS)
    parser.add_argument("--merchants", type=int, default=DEFAULT_MERCHANTS)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--check", action="store_true", help="no escribe: solo imprime la calibracion")
    args = parser.parse_args()

    rng = random.Random(args.seed)
    merchants = generate_merchants(rng, args.merchants)
    transactions = generate_transactions(rng, args.rows, merchants)
    injected = inject_problems(rng, transactions)
    measured = summarize(transactions)

    print(f"Semilla {args.seed} · {args.rows} autorizaciones · {args.merchants} comercios\n")
    print("Problemas de forma:")
    for key, label in (
        ("null_amount", "importe ausente"),
        ("duplicate_id_rows", "transaction_id duplicado"),
        ("non_canonical_method", "payment_method heredado"),
        ("non_canonical_status", "status sin normalizar"),
        ("non_iso_date", "fecha DD/MM/YYYY"),
    ):
        print(f"  {label:28s} medidos={measured[key]:>5}")

    print("\nProblemas de regla de negocio (propios de la adquirencia):")
    print(f"  {'libro descuadrado':28s} inyectados={injected['ledger_mismatch']:>4} "
          f"medidos={measured['ledger_mismatch']:>4}   net != amount - fee")
    print(f"  {'aprobada sin autorizacion':28s} inyectados={injected['approved_no_auth']:>4} "
          f"medidos={measured['approved_no_auth']:>4}   CAPTURED con response_code != 00")

    completeness = 1 - measured["null_amount"] / measured["rows"]
    print(f"\nCompleteness de amount: {completeness:.4f} (umbral del Lab 2: >= 0.95)")
    print(f"  -> la regla {'PASA' if completeness >= 0.95 else 'FALLA'}", end="")
    print(" justo en el borde: con un nulo mas fallaria"
          if abs(completeness - 0.95) < 0.001 else "")

    if args.check:
        print("\n--check: no se ha escrito nada.")
        return 0

    args.out.mkdir(parents=True, exist_ok=True)
    write_csv(args.out / "transactions.csv", transactions)
    write_csv(args.out / "merchants.csv", merchants)
    print(f"\nEscrito en {args.out}/")
    print("  transactions.csv   (columnas = acquiring.v_transactions_for_ingestion)")
    print("  merchants.csv      (nuestros comercios afiliados)")
    print("\nSi cambiaste las cantidades, ejecuta 'pytest tests/': los tests fijan")
    print("los numeros que citan los README del Lab 2.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
