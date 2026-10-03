#!/usr/bin/env python3
"""
generate_traffic.py — trafico de pagos realista contra la API de FinCorp.

Con el generador corriendo, el CDC del
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
    tarjeta sobre la MISMA intencion, como en Stripe. Y de vez en cuando la
    tienda devuelve, entera o a medias, una venta anterior.

  KYC
    De vez en cuando la tienda sin KYC intenta cobrar. La base se lo impide
    (409): ese rechazo tambien es un dato.

  TELEMETRIA (--telemetria)
    Cada terminal virtual publica un latido en <serial>/telemetry, con el mismo
    contrato que el firmware del Core2. Dos tienen problemas a proposito: un
    taxi con mala cobertura (senal baja, timeouts que dejan ventas abandonadas
    en la base) y otro que se queda sin bateria y deja de latir.

El Core2 real (TID00001) queda fuera a proposito: sus eventos alimentan el
dispositivo fisico de la demo, y el trafico simulado los ensuciaria.

Uso:
    # 1. Claves de las tiendas online (una vez por base de datos)
    python scripts/provision_demo_keys.py

    # 2. Trafico
    python scripts/generate_traffic.py --api http://localhost:8080 --count 200
    python scripts/generate_traffic.py --api "$API_URL" --duration 600
    python scripts/generate_traffic.py --api "$API_URL" --card-testing   # Lab 4
    python scripts/generate_traffic.py --api "$API_URL" --duration 900 --telemetria

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
P_DEVOLUCION = 0.04        # un paso de cada 25: una tienda devuelve una venta anterior
P_DEVOLUCION_PARCIAL = 0.3  # de esas, una parte se devuelve solo a medias


# ── Telemetria de la flota virtual ───────────────────────────────────────────
# El mismo contrato que publica el firmware del Core2 en <serial>/telemetry
# (payments/firmware/main/pos_telemetry.c). Los contadores son DELTAS desde el
# latido anterior, no acumulados: asi se suman en Athena sin restar latidos
# consecutivos, y un reinicio del terminal no produce un salto negativo.
TELEMETRY_SCHEMA = "1.0"

# Telemetria: (topic, mensaje). Lo que hay detras —IoT Core, un fichero, una
# lista en una prueba— no le importa al generador.
Publish = Callable[[str, dict], None]


@dataclass(frozen=True)
class Perfil:
    """Como se comporta la red y la bateria de un terminal virtual."""

    rssi_media: float = -58.0
    rssi_sd: float = 4.0
    # Probabilidad de que el POS se rinda esperando la respuesta de la API. El
    # cobro no llega a confirmarse y el cajero lo cancela.
    p_timeout: float = 0.0
    latencia_extra_ms: int = 0
    bateria_inicial: float = 100.0
    descarga_por_latido: float = 0.0


# Dos terminales con problemas, para que los labs tengan algo que encontrar:
#   TID00006 — un taxi con cobertura movil mala: senal baja y timeouts. Es el
#     insight "fallo la red, no el emisor" que cruza telemetria con cobros.
#   TID00007 — un taxi cuya bateria se agota: deja de latir y queda offline,
#     que es lo que detecta la tabla de estado del Lab 4.
PERFILES: dict[str, Perfil] = {
    "TID00006": Perfil(rssi_media=-84, rssi_sd=4, p_timeout=0.25, latencia_extra_ms=1800),
    "TID00007": Perfil(rssi_media=-66, bateria_inicial=22, descarga_por_latido=2.5),
}


@dataclass
class EstadoTerminal:
    tid: str
    serial: str
    perfil: Perfil
    bateria: float = 100.0
    arranque: float = 0.0
    peticiones: int = 0
    errores: int = 0
    timeouts: int = 0
    latencias: list[int] | None = None
    qr_mostrados: int = 0
    qr_pagados: int = 0
    qr_cancelados: int = 0

    def __post_init__(self):
        self.bateria = self.perfil.bateria_inicial
        self.latencias = []

    @property
    def apagado(self) -> bool:
        return self.bateria <= 0


def serial_virtual(tid: str) -> str:
    """El serial de un terminal virtual, como en 005_demo_portfolio.sql."""
    return f"sim-{tid.lower()}"


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
            # Si la red cae a mitad de la corrida, se cuenta y se sigue: un
            # traceback no ayuda a nadie.
            return 0, {}

    return send


def iot_publisher(region: str | None = None) -> Publish:
    """
    Publica en IoT Core por HTTPS, con tus credenciales de AWS.

    El Core2 publica por MQTT con su certificado; aqui no hay certificado de
    dispositivo, asi que se usa la API de datos de IoT. El mensaje llega a la
    misma regla y con el mismo topic: para el Lab 4 son indistinguibles.
    """
    import boto3

    endpoint = boto3.client("iot", region_name=region).describe_endpoint(
        endpointType="iot:Data-ATS")["endpointAddress"]
    client = boto3.client("iot-data", region_name=region, endpoint_url=f"https://{endpoint}")

    def publish(topic: str, mensaje: dict) -> None:
        try:
            client.publish(topic=topic, qos=1, payload=json.dumps(mensaje))
        except Exception as exc:  # noqa: BLE001 — un latido perdido no para la demo
            print(f"[!] telemetria no publicada en {topic}: {exc}", file=sys.stderr)

    return publish


def jsonl_publisher(ruta: Path) -> Publish:
    """Escribe la telemetria como la dejaria Firehose: un JSON por linea."""
    def publish(topic: str, mensaje: dict) -> None:
        # Lo que anade la regla de IoT (device_id, received_at_ms), para que el
        # fichero tenga la misma forma que raw/telemetry/.
        registro = {**mensaje, "device_id": topic.split("/")[0],
                    "received_at_ms": int(time.time() * 1000)}
        with ruta.open("a") as f:
            f.write(json.dumps(registro) + "\n")

    return publish


# ── Escenarios ───────────────────────────────────────────────────────────────
class Generador:
    def __init__(self, send: Send, claves: dict[str, str], rng: random.Random,
                 publish: Publish | None = None):
        self.send = send
        self.claves = claves
        self.rng = rng
        self.publish = publish
        self.resultado: Counter[str] = Counter()
        # Ventas online cobradas, para devolver alguna despues: (mid, ref, importe).
        self.cobradas: list[tuple[str, str, int]] = []
        self.flota = {
            tid: EstadoTerminal(tid, serial_virtual(tid), PERFILES.get(tid, Perfil()),
                                arranque=time.monotonic())
            for c in CARTERA for tid in c.terminales
        }

    def _send_terminal(self, t: EstadoTerminal, method: str, path: str,
                       body: dict | None, headers: dict) -> tuple[int, dict]:
        """Una llamada del terminal, medida como la mediria el firmware."""
        inicio = time.monotonic()
        status, cuerpo = self.send(method, path, body, headers)
        ms = int((time.monotonic() - inicio) * 1000) + t.perfil.latencia_extra_ms
        t.peticiones += 1
        t.latencias.append(ms)
        if status == 0 or status >= 500:
            t.errores += 1
        return status, cuerpo

    def latido(self) -> None:
        """Cada terminal encendido publica su estado y reinicia sus deltas."""
        if self.publish is None:
            return
        for t in self.flota.values():
            t.bateria = max(0.0, t.bateria - t.perfil.descarga_por_latido)
            if t.apagado:
                # Un terminal sin bateria no avisa de que se apaga: deja de
                # latir. Eso es exactamente lo que hay que saber detectar.
                continue
            lat = t.latencias or []
            self.publish(f"{t.serial}/telemetry", {
                "type": "heartbeat",
                "schema_version": TELEMETRY_SCHEMA,
                "terminal_id": t.tid,
                "firmware_version": "sim 1.0.0",
                "uptime_s": int(time.monotonic() - t.arranque),
                "battery_pct": round(t.bateria),
                "charging": False,
                "rssi_dbm": round(self.rng.gauss(t.perfil.rssi_media, t.perfil.rssi_sd)),
                "free_heap_bytes": self.rng.randint(80_000, 120_000),
                "http_requests": t.peticiones,
                "http_errors": t.errores,
                "http_timeouts": t.timeouts,
                "http_latency_ms_avg": round(sum(lat) / len(lat)) if lat else None,
                "http_latency_ms_max": max(lat) if lat else None,
                "qr_shown": t.qr_mostrados,
                "qr_paid": t.qr_pagados,
                "qr_canceled": t.qr_cancelados,
                # Un QR virtual no expira: o se paga, o se cancela.
                "qr_expired": 0,
            })
            t.peticiones = t.errores = t.timeouts = 0
            t.qr_mostrados = t.qr_pagados = t.qr_cancelados = 0
            t.latencias = []
            self.resultado["telemetria_latidos"] += 1

    # Utilidades
    def _ref(self) -> str:
        # uuid4 y no self.rng: la semilla reproduce las DECISIONES de una corrida,
        # no sus identificadores. Con referencias sembradas, repetir la corrida
        # contra la misma base chocaria con las intenciones de la anterior.
        return f"PI{uuid.uuid4().hex[:14].upper()}"

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
        encendidos = [tid for tid in c.terminales if not self.flota[tid].apagado]
        if not encendidos:
            self.resultado["pos_terminal_apagado"] += 1
            return
        t = self.flota[self.rng.choice(encendidos)]
        ref = self._ref()
        status, _ = self._send_terminal(t, "POST", "/v1/intents", {
            "intent_reference": ref, "terminal_id": t.tid,
            "amount_minor": self._importe(c), "currency_code": c.moneda,
        }, {"Idempotency-Key": f"gen-c-{ref}"})
        if status != 201:
            self._anotar(f"pos_crear_{status}", status)
            return

        es_qr = self.rng.random() < P_QR
        if es_qr:
            t.qr_mostrados += 1

        # La red del terminal falla esperando la confirmacion: el POS se rinde y
        # el cajero cancela. En la base queda una intencion abandonada; en la
        # telemetria, el timeout y la senal con la que ocurrio. Cruzar las dos
        # cosas es lo que dice "fallo la red, no el emisor".
        if self.rng.random() < t.perfil.p_timeout:
            t.timeouts += 1
            s, _ = self._send_terminal(t, "POST", f"/v1/intents/{ref}/cancel",
                                       {"cancellation_reason": "abandoned"}, {})
            if es_qr:
                t.qr_cancelados += 1
            self._anotar("pos_timeout", s)
            return

        if es_qr:
            if self.rng.random() < P_ABANDONO_QR:
                s, _ = self._send_terminal(t, "POST", f"/v1/intents/{ref}/cancel",
                                           {"cancellation_reason": "abandoned"}, {})
                t.qr_cancelados += 1
                self._anotar("qr_abandonado", s)
                return
            s, body = self._send_terminal(t, "POST", f"/v1/intents/{ref}/confirm",
                                          {"channel": "QR"}, {"Idempotency-Key": f"gen-q-{ref}"})
            if body.get("response_code") == "00":
                t.qr_pagados += 1
            self._anotar(f"qr_{body.get('response_code', s)}", s)
            return

        cvv = "000" if self.rng.random() < P_CVV_MAL else "123"
        s, body = self._send_terminal(t, "POST", f"/v1/intents/{ref}/confirm", {
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
        importe = self._importe(c)
        status, _ = self.send("POST", "/v1/intents", {
            "intent_reference": ref, "amount_minor": importe,
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
                    self.cobradas.append((c.mid, ref, importe))
                    return
            else:
                self._anotar(f"online_{body.get('response_code', s)}", s)
                if body.get("status") == "CAPTURED":
                    self.cobradas.append((c.mid, ref, importe))
                    return
                if s != 200:
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

    def card_testing(self, intentos: int = 16, pausa: float = 1.0) -> None:
        """
        Un ataque de card testing contra una tienda online, por la API real.

        Quien prueba tarjetas robadas hace muchas compras pequenas seguidas, y la
        mayoria se declinan porque esas tarjetas ya estan bloqueadas. Contra
        PixelPlay (bienes digitales, riesgo alto: se revenden al instante) es el
        caso de libro. Dispara DECLINE_RATIO y VELOCITY_COUNT en el Lab 4, y como
        pasa por la API, tambien queda en la base, en el CDC y en el lago.
        """
        c = next(x for x in CARTERA if x.mcc == "5816")
        clave = self.claves.get(c.mid)
        if clave is None:
            self.resultado["ataque_sin_clave"] += 1
            return
        auth = {"Authorization": f"Bearer {clave}"}
        robadas = ("pm_card_visa_chargeDeclined", "pm_card_visa_chargeDeclinedInsufficientFunds",
                   "pm_card_chargeDeclinedExpiredCard")
        for i in range(intentos):
            ref = self._ref()
            # Importes de prueba: 1 a 5 USD. Nadie arriesga una compra grande
            # con una tarjeta que no sabe si funciona.
            s, _ = self.send("POST", "/v1/intents", {
                "intent_reference": ref, "amount_minor": self.rng.randint(100, 500),
                "currency_code": c.moneda,
            }, {**auth, "Idempotency-Key": f"gen-a-{ref}"})
            if s != 201:
                self._anotar(f"ataque_crear_{s}", s)
                continue
            # Una de cada cinco "funciona": es la que el atacante buscaba.
            pm = "pm_card_visa" if i % 5 == 4 else self.rng.choice(robadas)
            s, body = self.send("POST", f"/v1/intents/{ref}/confirm", {
                "channel": "CARD_NOT_PRESENT", "payment_method": pm,
            }, {**auth, "Idempotency-Key": f"gen-ac-{ref}"})
            self._anotar(f"ataque_{body.get('response_code', s)}", s)
            if pausa:
                time.sleep(pausa)

    def devolucion(self) -> None:
        """
        Una tienda devuelve una venta ANTERIOR, no la que acaba de hacer.

        Es el caso que justifica el CDC: el cobro ya esta en el lago, y ahora
        cambia. Un incremental que pregunta "que hay nuevo" no lo veria.
        """
        if not self.cobradas:
            return
        mid, ref, importe = self.cobradas.pop(self.rng.randrange(len(self.cobradas)))
        body = {"payment_intent": ref,
                "reason": self.rng.choice(["requested_by_customer", "requested_by_customer",
                                           "duplicate", "fraudulent"])}
        if self.rng.random() < P_DEVOLUCION_PARCIAL and importe > 200:
            body["amount_minor"] = max(100, importe // 3)
        s, _ = self.send("POST", "/v1/refunds", body, {
            "Authorization": f"Bearer {self.claves[mid]}",
            "Idempotency-Key": f"gen-r-{ref}",
        })
        self._anotar("devolucion_parcial" if "amount_minor" in body else "devolucion_total", s)
        if s != 201:
            self._anotar(f"devolucion_{s}", s)

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
        if online and self.cobradas and self.rng.random() < P_DEVOLUCION:
            self.devolucion()
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
        pos: bool = True, online: bool = True, progress: bool = False,
        ataque: bool = False, publish: Publish | None = None,
        latido_s: float = 30.0, latido_cada: int = 20) -> Counter:
    """
    Corre el generador. Devuelve el recuento por resultado.

    Con `publish`, la flota virtual late cada `latido_s` segundos de reloj; sin
    ritmo (rate=0, las pruebas) late cada `latido_cada` operaciones, porque el
    reloj apenas avanza.
    """
    gen = Generador(send, claves, random.Random(seed), publish)
    if ataque:
        gen.card_testing(pausa=1.0 if rate > 0 else 0.0)
        gen.resultado["operaciones"] = 0
        return gen.resultado
    inicio = time.monotonic()
    ultimo_latido = inicio
    n = 0
    gen.latido()
    while True:
        if count is not None and n >= count:
            break
        if duration is not None and time.monotonic() - inicio >= duration:
            break
        gen.paso(pos=pos, online=online)
        n += 1
        ahora = time.monotonic()
        if (rate > 0 and ahora - ultimo_latido >= latido_s) or (rate <= 0 and n % latido_cada == 0):
            gen.latido()
            ultimo_latido = ahora
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
    # 0.5/s reparte ~2 ventas por minuto a cada terminal virtual: una tienda
    # concurrida. Mas rapido, cada terminal pasaria el umbral de velocidad del
    # Lab 4 (5 por minuto) y todo seria alerta, que es lo mismo que ninguna.
    p.add_argument("--rate", type=float, default=0.5, help="operaciones por segundo (media)")
    p.add_argument("--seed", type=int, help="semilla, para repetir exactamente una corrida")
    p.add_argument("--solo-pos", action="store_true", help="solo mostrador")
    p.add_argument("--solo-online", action="store_true", help="solo online")
    p.add_argument("--telemetria", action="store_true",
                   help="la flota virtual publica su telemetria en IoT Core (necesita boto3 y credenciales)")
    p.add_argument("--telemetria-archivo", type=Path,
                   help="en vez de IoT Core, escribe la telemetria como JSONL en este fichero")
    p.add_argument("--card-testing", action="store_true",
                   help="en vez de trafico normal, un ataque de card testing contra PixelPlay")
    args = p.parse_args()

    if args.count is None and args.duration is None:
        args.count = 100
    claves: dict[str, str] = {}
    if args.keys.is_file():
        claves = json.loads(args.keys.read_text())
    elif not args.solo_pos:
        print(f"[!] No hay claves en {args.keys}: solo habra trafico de mostrador.\n"
              f"    Crealas con: python scripts/provision_demo_keys.py", file=sys.stderr)

    publish = None
    if args.telemetria_archivo:
        publish = jsonl_publisher(args.telemetria_archivo)
    elif args.telemetria:
        publish = iot_publisher()

    print(f"Generando trafico contra {args.api} …", file=sys.stderr)
    res = run(http_sender(args.api), claves, count=args.count, duration=args.duration,
              rate=args.rate, seed=args.seed, pos=not args.solo_online,
              online=not args.solo_pos and bool(claves), progress=True,
              ataque=args.card_testing, publish=publish)

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
