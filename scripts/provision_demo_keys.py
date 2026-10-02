#!/usr/bin/env python3
"""
provision_demo_keys.py — crea las claves de API de las tiendas online de la demo.

Las claves NO estan en el repositorio ni en el seed: una clave publicada en
GitHub serviria contra cualquier despliegue de este taller. Se crean contra TU
base, se guarda su hash, y el texto en claro va a `.demo_keys.json`, que esta
en .gitignore. Es lo que lee generate_traffic.py.

La base esta en una subred privada. Desde tu maquina, abre antes un tunel:

    # El comando exacto lo imprime: terraform -chdir=infra/payments output db_tunnel_command
    aws ssm start-session --target <instancia> \\
        --document-name AWS-StartPortForwardingSession \\
        --parameters 'portNumber=5432,localPortNumber=15432'

y luego:

    DB_HOST=localhost DB_PORT=15432 DB_PASSWORD=... python scripts/provision_demo_keys.py

Reejecutarlo crea claves NUEVAS (las anteriores siguen validas hasta que se
revoquen). No hay forma de recuperar una clave perdida: solo existe su hash.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "payments" / "backend"))

# Las tiendas que cobran online, y la que no tiene KYC (para que el generador
# pueda intentarlo y registrar el 409). Ver payments/schema/005_demo_portfolio.sql.
TIENDAS_ONLINE = (
    "MID000000020006",
    "MID000000020007",
    "MID000000020008",
    "MID000000020009",
    "MID000000020010",
    "MID000000020011",
)


def main() -> int:
    p = argparse.ArgumentParser(description="Crea las claves de API de la demo")
    p.add_argument("--out", type=Path, default=REPO_ROOT / ".demo_keys.json")
    args = p.parse_args()

    try:
        import psycopg
    except ImportError:
        print("Falta psycopg: pip install 'psycopg[binary]'", file=sys.stderr)
        return 1
    from app.auth import create_api_key
    from app.config import get_settings

    settings = get_settings()
    if not os.getenv("DB_PASSWORD") and not settings.db_secret_arn:
        print("Falta DB_PASSWORD (o DB_SECRET_ARN).", file=sys.stderr)
        return 1

    claves: dict[str, str] = {}
    with psycopg.connect(settings.db_conninfo, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT merchant_id, id FROM acquiring.merchants WHERE merchant_id = ANY(%s)",
                (list(TIENDAS_ONLINE),),
            )
            pks = dict(cur.fetchall())
        faltan = set(TIENDAS_ONLINE) - set(pks)
        if faltan:
            print(f"Faltan comercios en la base: {sorted(faltan)}.\n"
                  "Aplica payments/schema/005_demo_portfolio.sql.", file=sys.stderr)
            return 1
        for mid in TIENDAS_ONLINE:
            claves[mid] = create_api_key(conn, pks[mid], "generate_traffic.py")

    args.out.write_text(json.dumps(claves, indent=2) + "\n")
    # Solo el dueño puede leerlo: son credenciales, aunque sean de una demo.
    args.out.chmod(0o600)
    print(f"[✓] {len(claves)} claves en {args.out} (prefijos: "
          f"{', '.join(k[:12] for k in claves.values())})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
