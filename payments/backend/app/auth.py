"""
Autenticacion del comercio con claves de API, como `Authorization: Bearer sk_...`
en la API de Stripe.

Dos formas de llegar a la API, y no se mezclan:

  - El POS se identifica por su TERMINAL. El comercio sale del terminal, no de
    una clave: un dispositivo en un mostrador no guarda secretos de servidor.
  - Una tienda online se identifica con su CLAVE, desde su servidor. Sin
    terminal, la clave es lo unico que dice de quien es el cobro.

DEMO: el terminal no se autentica. En un adquirente real lo haria con un
certificado de dispositivo —el ATECC608 del Core2 sirve justamente para eso—, y
queda fuera del alcance del taller.

La clave nunca se guarda: se guarda su SHA-256. Ver merchant_api_keys en
payments/schema/003_merchant_interface.sql.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

from psycopg import Connection
from psycopg.rows import dict_row

from .acquiring import AcquiringError

KEY_PREFIX = "sk_demo_"
# key_prefix es VARCHAR(12): el prefijo fijo mas 4 caracteres de la parte
# aleatoria, que bastan para que el comercio reconozca la clave en su panel.
VISIBLE_PREFIX_LEN = 12

SCOPE_WRITE_INTENTS = "write:payment_intents"


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def create_api_key(
    conn: Connection,
    merchant_pk: int,
    label: str,
    scopes: list[str] | None = None,
) -> str:
    """
    Crea una clave y devuelve el texto en claro. Es la UNICA vez que existe.

    Si se pierde, se revoca y se crea otra: no hay forma de recuperarla, porque
    la base solo tiene el hash.
    """
    scopes = scopes or [SCOPE_WRITE_INTENTS, "read:charges"]
    for _ in range(5):
        key = KEY_PREFIX + secrets.token_urlsafe(24).replace("-", "x").replace("_", "y")
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO acquiring.merchant_api_keys
                    (merchant_id, key_prefix, key_hash, label, scopes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (key_prefix) DO NOTHING
                RETURNING id
                """,
                (merchant_pk, key[:VISIBLE_PREFIX_LEN], _hash(key), label, scopes),
            )
            if cur.fetchone() is not None:
                return key
    # Cinco colisiones de un prefijo de 4 caracteres seguidas no ocurren por azar.
    raise RuntimeError("no se pudo generar un prefijo de clave unico")


def parse_bearer(authorization: str | None) -> str | None:
    """Extrae la clave de la cabecera. None si no hay cabecera."""
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AcquiringError("cabecera Authorization mal formada: se espera 'Bearer <clave>'", 401)
    return token.strip()


def authenticate(conn: Connection, key: str, required_scope: str) -> dict:
    """
    Devuelve el comercio duenio de la clave, o lanza 401/403.

    El mensaje de 401 es el mismo exista o no el prefijo: distinguirlos le diria
    a un atacante que prefijos son validos.
    """
    if not key.startswith(KEY_PREFIX) or len(key) <= VISIBLE_PREFIX_LEN:
        raise AcquiringError("clave de API invalida", 401)

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            """
            SELECT k.id, k.key_hash, k.scopes, m.merchant_id
            FROM acquiring.merchant_api_keys k
            JOIN acquiring.merchants m ON m.id = k.merchant_id
            WHERE k.key_prefix = %s
              AND k.revoked_at IS NULL
              AND (k.expires_at IS NULL OR k.expires_at > NOW())
            """,
            (key[:VISIBLE_PREFIX_LEN],),
        )
        row = cur.fetchone()

    # compare_digest: comparar hashes con == filtra por tiempo cuantos
    # caracteres coinciden.
    if row is None or not hmac.compare_digest(row["key_hash"], _hash(key)):
        raise AcquiringError("clave de API invalida", 401)
    if required_scope not in row["scopes"]:
        raise AcquiringError(f"la clave no tiene el permiso {required_scope}", 403)

    with conn.cursor() as cur:
        cur.execute(
            "UPDATE acquiring.merchant_api_keys SET last_used_at = NOW() WHERE id = %s",
            (row["id"],),
        )
    return {"merchant_id": row["merchant_id"]}
