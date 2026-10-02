#!/usr/bin/env python3
"""
generate_traffic.py — trafico de pagos realista contra la API de FinCorp.

Sirve para ensayar la demo sin publico: con el generador corriendo, el CDC del
Lab 1 tiene deltas que mostrar, el Lab 2 tiene canales y comercios que comparar,
y el scoring del Lab 4 tiene velocidad que medir.

Habla con la API por HTTP, exactamente como lo harian el POS y una tienda
online. NO escribe en la base: si lo hiciera se saltaria las constraints, la
idempotencia y el libro mayor, que es justo lo que los labs analizan.

Que genera, y por que cada cosa:

  MOSTRADOR (terminales virtuales sim-*)
    QR ................. cobros que el comprador confirma desde el movil
    tarjeta presente ... CVV correcto casi siempre; a veces no -> 05
    abandono ........... un QR que nadie paga y el cajero cancela

  ONLINE (con la clave de API de cada tienda)
    PaymentMethods de prueba al estilo Stripe: pm_card_visa la mayoria de veces,
    declinaciones 05/51/54, y 3DS (pm_card_threeDSecure2Required) que el
    comprador supera o no. Tras una declinacion, a veces reintenta con otra
    tarjeta sobre la MISMA intencion, como en Stripe.

  KYC
    De vez en cuando la tienda sin KYC intenta cobrar. La base se lo impide
    (409): ese rechazo tambien es un dato.

El Core2 real (TID00001) queda fuera a proposito: sus eventos alimentan el
dispositivo fisico de la demo, y el trafico simulado los ensuciaria.

Uso:
    # 1. Claves de las tiendas online (una vez por base de datos)
    python scripts/provision_demo_keys.py

    # 2. Trafico
    python scripts/generate_traffic.py --api http://localhost:8080 --count 200
    python scripts/generate_traffic.py --api "$API_URL" --duration 600 --rate 1

Solo biblioteca estandar: corre en cualquier maquina con Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs, urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_KEYS = REPO_ROOT / ".demo_keys.json"

# (status, cuerpo) — la forma en que el generador ve cualquier respuesta. Asi el
# transporte se cambia sin tocar los escenarios: urllib contra un servidor, o el
# TestClient de FastAPI en las pruebas.
Send = Callable[[str, str, dict | None, dict], tuple[int, dict]]


# ── La cartera (payments/schema/005_demo_portfolio.sql) ──────────────────────
@dataclass(frozen=True)
class Comercio:
    mid: str
    nombre: str
    mcc: str
    moneda: str
    # Mediana del ticket en unidades menores, y cuanto se dispersa (sigma de una
    # lognormal). Un taxi cobra parecido siempre; una casa de cambio no.
    mediana: int
    dispersion: float
    terminales: tuple[str, ...] = ()
    online: bool = False
    riesgo_alto: bool = False


CARTERA: tuple[Comercio, ...] = (
    Comercio("MID000000020001", "Picanteria Arequipa", "5812", "PEN", 6500, 0.5,
             ("TID00002", "TID00003")),
    Comercio("MID000000020002", "Minimarket San Isidro", "5411", "PEN", 3500, 0.7, ("TID00004",)),
    Comercio("MID000000020003", "Farmacia Vida", "5912", "PEN", 4800, 0.6, ("TID00005",)),
    Comercio("MID000000020004", "Taxi Seguro Lima", "4121", "PEN", 1800, 0.3,
             ("TID00006", "TID00007")),
    Comercio("MID000000020005", "Casa de Cambio Sol", "6051", "USD", 45000, 1.0, ("TID00008",),
             riesgo_alto=True),
    Comercio("MID000000020006", "Hotel Miraflores Suites", "7011", "USD", 28000, 0.6, ("TID00009",),
             online=True),
    Comercio("MID000000020007", "TecnoMarket Online", "5732", "PEN", 120000, 0.8, online=True),
    Comercio("MID000000020008", "Moda Urbana Online", "5691", "PEN", 15000, 0.6, online=True),
    Comercio("MID000000020009", "Rutas del Inca Viajes", "4722", "USD", 60000, 0.7, online=True),
    Comercio("MID000000020010", "PixelPlay Store", "5816", "USD", 2000, 0.9, online=True,
             riesgo_alto=True),
)
SIN_KYC = "MID000000020011"

# Como pagan los compradores online. Los pesos son la mezcla de la demo, no un
# dato de mercado: lo bastante sesgada hacia la aprobacion para parecer real, y
# con cada camino frecuente como para verse en unos pocos cientos de pagos.
TOKENS_ONLINE = (
    ("pm_card_visa", 0.78),
    ("pm_card_visa_chargeDeclined", 0.05),
    ("pm_card_visa_chargeDeclinedInsufficientFunds", 0.05),
    ("pm_card_chargeDeclinedExpiredCard", 0.03),
    ("pm_card_threeDSecure2Required", 0.09),
)

P_QR = 0.6                 # en mostrador, QR frente a tarjeta presente
P_CVV_MAL = 0.08           # tarjeta presente con CVV equivocado -> 05
P_ABANDONO_QR = 0.07       # QR en pantalla que nadie paga
P_ABANDONO_ONLINE = 0.06   # carrito que se abre y no se paga
P_3DS_OK = 0.85            # comprador que supera la autenticacion
P_REINTENTO = 0.5          # tras declinar, prueba con otra tarjeta
P_SIN_KYC = 0.01           # la tienda sin KYC intenta cobrar
P_ATIPICO = 0.01           # importe x20 en comercios de riesgo alto


# ── Transporte HTTP real ─────────────────────────────────────────────────────
def http_sender(base_url: str, timeout: float = 15.0) -> Send:
    base = base_url.rstrip("/")

    def send(method: str, path: str, body: dict | None, headers: dict) -> tuple[int, dict]:
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            base + path, data=data, method=method,
            headers={"Content-Type": "application/json", **headers},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as err:
            try:
                return err.code, json.loads(err.read() or b"{}")
            except json.JSONDecodeError:
                return err.code, {}
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            # La red cae a mitad de la demo: se cuenta y se sigue. Un traceback
            # en pantalla delante del publico no ayuda a nadie.
            return 0, {}

    return send


# ── Escenarios ───────────────────────────────────────────────────────────────
class Generador:
    def __init__(self, send: Send, claves: dict[str, str], rng: random.Random):
        self.send = send
        self.claves = claves
        self.rng = rng
        self.resultado: Counter[str] = Counter()

    # Utilidades
    def _ref(self) -> str:
        return f"PI{uuid.UUID(int=self.rng.getrandbits(128)).hex[:14].upper()}"

    def _importe(self, c: Comercio) -> int:
        valor = c.mediana * math.exp(self.rng.gauss(0, c.dispersion))
        if c.riesgo_alto and self.rng.random() < P_ATIPICO:
            valor *= 20
        return max(100, min(int(valor), 99_999_999))

    def _anotar(self, clave: str, status: int) -> None:
        if status >= 500:
            self.resultado["error_5xx"] += 1
        elif status == 0:
            self.resultado["error_red"] += 1
        self.resultado[clave] += 1

    # Mostrador
    def venta_mostrador(self, c: Comercio) -> None:
        ref = self._ref()
        tid = self.rng.choice(c.terminales)
        status, _ = self.send("POST", "/v1/intents", {
            "intent_reference": ref, "terminal_id": tid,
            "amount_minor": self._importe(c), "currency_code": c.moneda,
        }, {"Idempotency-Key": f"gen-c-{ref}"})
        if status != 201:
            self._anotar(f"pos_crear_{status}", status)
            return

        if self.rng.random() < P_QR:
            if self.rng.random() < P_ABANDONO_QR:
                s, _ = self.send("POST", f"/v1/intents/{ref}/cancel",
                                 {"cancellation_reason": "abandoned"}, {})
                self._anotar("qr_abandonado", s)
                return
            s, body = self.send("POST", f"/v1/intents/{ref}/confirm",
                                {"channel": "QR"}, {"Idempotency-Key": f"gen-q-{ref}"})
            self._anotar(f"qr_{body.get('response_code', s)}", s)
            return

        cvv = "000" if self.rng.random() < P_CVV_MAL else "123"
        s, body = self.send("POST", f"/v1/intents/{ref}/confirm", {
            "channel": "CARD_PRESENT", "card_token": "demo_card_4242", "cvv": cvv,
        }, {"Idempotency-Key": f"gen-t-{ref}"})
        self._anotar(f"tarjeta_{body.get('response_code', s)}", s)

    # Online
    def _token(self) -> str:
        tokens, pesos = zip(*TOKENS_ONLINE)
        return self.rng.choices(tokens, weights=pesos)[0]

    def venta_online(self, c: Comercio) -> None:
        clave = self.claves.get(c.mid)
        if clave is None:
            self.resultado["online_sin_clave"] += 1
            return
        auth = {"Authorization": f"Bearer {clave}"}
        ref = self._ref()
        status, _ = self.send("POST", "/v1/intents", {
            "intent_reference": ref, "amount_minor": self._importe(c),
            "currency_code": c.moneda, "return_url": "https://tienda.example/gracias",
        }, {**auth, "Idempotency-Key": f"gen-c-{ref}"})
        if status != 201:
            self._anotar(f"online_crear_{status}", status)
            return

        if self.rng.random() < P_ABANDONO_ONLINE:
            s, _ = self.send("POST", f"/v1/intents/{ref}/cancel",
                             {"cancellation_reason": "abandoned"}, auth)
            self._anotar("online_abandonado", s)
            return

        token = self._token()
        for intento in range(2):
            s, body = self.send("POST", f"/v1/intents/{ref}/confirm", {
                "channel": "CARD_NOT_PRESENT", "payment_method": token,
            }, {**auth, "Idempotency-Key": f"gen-o{intento}-{ref}"})

            if body.get("status") == "REQUIRES_ACTION":
                body = self._autenticar(ref, body)
                if body.get("status") == "CAPTURED":
                    return
            else:
                self._anotar(f"online_{body.get('response_code', s)}", s)
                if body.get("status") == "CAPTURED" or s != 200:
                    return

            # Declinada o 3DS fallido: a veces el comprador prueba otra tarjeta.
            if intento == 0 and self.rng.random() < P_REINTENTO:
                self.resultado["online_reintento"] += 1
                token = "pm_card_visa"
                continue
            return

    def _autenticar(self, ref: str, body: dict) -> dict:
        """Hace de navegador del comprador en la pagina del banco simulado."""
        url = body["next_action"]["redirect_to_url"]["url"]
        token = parse_qs(urlparse(url).query)["t"][0]
        ok = self.rng.random() < P_3DS_OK
        s, res = self.send("POST", f"/v1/intents/{ref}/authenticate",
                           {"token": token, "authenticated": ok}, {})
        self._anotar("online_3ds_ok" if ok else "online_3ds_fallido", s)
        return res

    def intento_sin_kyc(self) -> None:
        clave = self.claves.get(SIN_KYC)
        if clave is None:
            return
        ref = self._ref()
        s, _ = self.send("POST", "/v1/intents", {
            "intent_reference": ref, "amount_minor": 5000, "currency_code": "PEN",
        }, {"Authorization": f"Bearer {clave}", "Idempotency-Key": f"gen-k-{ref}"})
        self._anotar(f"sin_kyc_{s}", s)

    # Un paso: elige que pasa ahora
    def paso(self, pos: bool = True, online: bool = True) -> None:
        if online and self.rng.random() < P_SIN_KYC:
            self.intento_sin_kyc()
            return
        candidatos = [c for c in CARTERA
                      if (pos and c.terminales) or (online and c.online)]
        c = self.rng.choice(candidatos)
        usa_online = online and c.online and (not (pos and c.terminales) or self.rng.random() < 0.5)
        if usa_online:
            self.venta_online(c)
        else:
            self.venta_mostrador(c)


def run(send: Send, claves: dict[str, str], count: int | None = None,
        duration: float | None = None, rate: float = 0.0, seed: int | None = None,
        pos: bool = True, online: bool = True, progress: bool = False) -> Counter:
    """Corre el generador. Devuelve el recuento por resultado."""
    gen = Generador(send, claves, random.Random(seed))
    inicio = time.monotonic()
    n = 0
    while True:
        if count is not None and n >= count:
            break
        if duration is not None and time.monotonic() - inicio >= duration:
            break
        gen.paso(pos=pos, online=online)
        n += 1
        if progress and n % 25 == 0:
            print(f"  {n} operaciones…", file=sys.stderr)
        if rate > 0:
            # Intervalos exponenciales: llegadas de Poisson, como las de verdad.
            # Un ritmo fijo haria que el scoring del Lab 4 viera un patron
            # artificial de una operacion exacta cada N segundos.
            time.sleep(gen.rng.expovariate(rate))
    gen.resultado["operaciones"] = n
    return gen.resultado


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--api", default="http://localhost:8080", help="URL base de la API")
    p.add_argument("--keys", type=Path, default=DEFAULT_KEYS,
                   help="claves de las tiendas online (provision_demo_keys.py)")
    p.add_argument("--count", type=int, help="numero de operaciones")
    p.add_argument("--duration", type=float, help="segundos de trafico")
    p.add_argument("--rate", type=float, default=2.0, help="operaciones por segundo (media)")
    p.add_argument("--seed", type=int, help="semilla, para repetir exactamente una corrida")
    p.add_argument("--solo-pos", action="store_true", help="solo mostrador")
    p.add_argument("--solo-online", action="store_true", help="solo online")
    args = p.parse_args()

    if args.count is None and args.duration is None:
        args.count = 100
    claves: dict[str, str] = {}
    if args.keys.is_file():
        claves = json.loads(args.keys.read_text())
    elif not args.solo_pos:
        print(f"[!] No hay claves en {args.keys}: solo habra trafico de mostrador.\n"
              f"    Crealas con: python scripts/provision_demo_keys.py", file=sys.stderr)

    print(f"Generando trafico contra {args.api} …", file=sys.stderr)
    res = run(http_sender(args.api), claves, count=args.count, duration=args.duration,
              rate=args.rate, seed=args.seed, pos=not args.solo_online,
              online=not args.solo_pos and bool(claves), progress=True)

    operaciones = res.pop("operaciones", 0)
    ancho = max((len(k) for k in res), default=10)
    # Una operacion puede dejar varios resultados: una declinacion seguida de un
    # reintento aprobado cuenta en online_05, online_reintento y online_00.
    print(f"\nResultado de {operaciones} operaciones")
    for k, v in sorted(res.items()):
        print(f"  {k:<{ancho}}  {v:>6}")
    if res.get("error_red"):
        print(f"\n[!] {res['error_red']} operaciones sin respuesta: ¿la API esta arriba?",
              file=sys.stderr)
    if operaciones and res.get("error_red", 0) >= operaciones:
        return 1
    if res.get("error_5xx"):
        print(f"\n[✗] {res['error_5xx']} respuestas 5xx: revisa el log del backend.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
