"""
Idempotencia. Sin esto, un Wi-Fi inestable cobra dos veces.

El POS de la charla va a reintentar: la red del evento no es de fiar. Un
reintento de "confirmar pago" NO puede generar un segundo cobro.

Como funciona, que es el patron estandar de las APIs de pago:

  1. El cliente manda una cabecera Idempotency-Key.
  2. Se intenta INSERT de la clave. Si ya existe:
       - respuesta guardada  -> se devuelve tal cual, sin reejecutar nada
       - en curso            -> 409, hay otra peticion trabajando
  3. Si el INSERT funciona, se ejecuta la operacion y se guarda la respuesta.

El fingerprint del cuerpo es lo que evita el error mas sutil: reusar la misma
clave con un cuerpo distinto. Eso es un bug del cliente y hay que gritarlo, no
devolverle la respuesta de la peticion anterior.
"""

from __future__ import annotations

import hashlib
import json
import logging

from psycopg import Connection
from psycopg.rows import dict_row

log = logging.getLogger(__name__)


class IdempotencyConflict(Exception):
    """Misma clave, cuerpo distinto, o una peticion aun en curso."""

    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.status_code = status_code


def fingerprint(body: dict) -> str:
    """Hash estable del cuerpo. sort_keys para que el orden no lo cambie."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def begin(
    conn: Connection,
    merchant_pk: int | None,
    key: str,
    method: str,
    path: str,
    body: dict,
) -> dict | None:
    """
    Reserva la clave.

    Devuelve None si es la primera vez (hay que ejecutar la operacion), o la
    respuesta guardada si ya se ejecuto.
    """
    fp = fingerprint(body)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            INSERT INTO acquiring.idempotency_keys
                (merchant_id, idempotency_key, request_method, request_path,
                 request_fingerprint, status, locked_at)
            VALUES (%s,%s,%s,%s,%s,'IN_PROGRESS',NOW())
            ON CONFLICT (merchant_id, idempotency_key) DO NOTHING
            RETURNING id
            """,
            (merchant_pk, key, method, path, fp),
        )
        if cur.fetchone() is not None:
            return None  # primera vez

        # Ya existia: hay que mirar en que estado esta.
        cur.execute(
            """
            SELECT status, request_fingerprint, response_status, response_body
            FROM acquiring.idempotency_keys
            WHERE merchant_id = %s AND idempotency_key = %s
            """,
            (merchant_pk, key),
        )
        existing = cur.fetchone()

    if existing["request_fingerprint"] != fp:
        # 409 y no 422 a proposito: 422 es el codigo con el que FastAPI
        # responde a un cuerpo mal formado, y el POS es firmware embebido que
        # decide por el status sin parsear el JSON. Si los dos casos comparten
        # codigo, no puede distinguir "tu peticion esta mal" de "reusaste una
        # clave", que piden reacciones opuestas: la primera no se reintenta, la
        # segunda se reintenta con clave nueva.
        raise IdempotencyConflict(
            "la clave de idempotencia ya se uso con un cuerpo distinto", 409
        )

    if existing["status"] == "IN_PROGRESS":
        raise IdempotencyConflict(
            "hay otra peticion en curso con esa clave; reintenta en unos segundos", 409
        )

    log.info("respuesta idempotente reutilizada para la clave %s", key)
    return {
        "status_code": existing["response_status"],
        "body": existing["response_body"],
    }


def complete(
    conn: Connection,
    merchant_pk: int | None,
    key: str,
    status_code: int,
    body: dict,
    resource_type: str | None = None,
    resource_id: str | None = None,
) -> None:
    """Guarda la respuesta para que el siguiente reintento la reutilice."""
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE acquiring.idempotency_keys
            SET status = 'COMPLETED', response_status = %s, response_body = %s,
                resource_type = %s, resource_id = %s, locked_at = NULL
            WHERE merchant_id = %s AND idempotency_key = %s
            """,
            (status_code, json.dumps(body, default=str), resource_type,
             resource_id, merchant_pk, key),
        )


def release(conn: Connection, merchant_pk: int | None, key: str) -> None:
    """
    Libera la clave si la operacion fallo por un error inesperado.

    Sin esto, un fallo transitorio dejaria la clave bloqueada en IN_PROGRESS y el
    reintento legitimo recibiria un 409 para siempre.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            DELETE FROM acquiring.idempotency_keys
            WHERE merchant_id = %s AND idempotency_key = %s AND status = 'IN_PROGRESS'
            """,
            (merchant_pk, key),
        )
