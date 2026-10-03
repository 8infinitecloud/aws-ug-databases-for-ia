#!/usr/bin/env python3
"""
provision_demo_keys.py — crea las claves de API de las tiendas online de la demo.

Las claves NO estan en el repositorio ni en el seed: una clave publicada en
GitHub serviria contra cualquier despliegue de este taller. Se crean contra TU
base, se guarda su hash, y el texto en claro va a `.demo_keys.json`, que esta
en .gitignore. Es lo que lee generate_traffic.py.

Contra la base de AWS, lo mas sencillo es por SSM, sin tunel:

    python scripts/provision_demo_keys.py --ssm

Las claves se generan en tu maquina y a la base solo viajan sus hashes.

O con un tunel (necesita el session-manager-plugin):

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


def comando_psql(sentencias: list[str]) -> str:
    """
    Un -c por sentencia, y no todas en uno: con varias en un solo -c, psql solo
    imprime el resultado de la ultima, y no se podria comprobar que entraron
    todas. Las sentencias solo llevan comillas simples, asi que las dobles del
    shell las envuelven sin escapar nada.
    """
    return "sudo -u postgres psql -d acquiring -v ON_ERROR_STOP=1 " + " ".join(
        f'-c "{sql}"' for sql in sentencias)


def provision_via_ssm(out: Path, region: str) -> int:
    """
    Sin tunel: las claves se generan AQUI y a la base solo viaja su hash.

    Le pide a la instancia de la base, por SSM Run Command, que inserte el hash
    de cada clave. No hace falta el session-manager-plugin ni abrir un puerto,
    y la clave en claro nunca sale de esta maquina: ni por la red ni en el
    historial de comandos de SSM.
    """
    import secrets
    import subprocess
    import time

    import boto3

    from app.auth import KEY_PREFIX, SCOPE_WRITE_INTENTS, VISIBLE_PREFIX_LEN, _hash

    instancia = subprocess.run(
        ["terraform", f"-chdir={REPO_ROOT / 'infra/payments'}", "output", "-raw", "db_instance_id"],
        capture_output=True, text=True,
    ).stdout.strip()
    if not instancia.startswith("i-"):
        print("No se encontro la instancia de la base: ¿infra/payments esta desplegado?",
              file=sys.stderr)
        return 1

    claves, sentencias = {}, []
    for mid in TIENDAS_ONLINE:
        clave = KEY_PREFIX + secrets.token_urlsafe(24).replace("-", "x").replace("_", "y")
        claves[mid] = clave
        # Los valores son nuestros (MID fijo, prefijo y hash hexadecimales): no
        # hay entrada de usuario que escapar.
        sentencias.append(
            "INSERT INTO acquiring.merchant_api_keys (merchant_id, key_prefix, key_hash, label, scopes) "
            f"SELECT id, '{clave[:VISIBLE_PREFIX_LEN]}', '{_hash(clave)}', 'generate_traffic.py', "
            f"ARRAY['{SCOPE_WRITE_INTENTS}','read:charges'] "
            f"FROM acquiring.merchants WHERE merchant_id = '{mid}';"
        )

    ssm = boto3.client("ssm", region_name=region)
    cmd = ssm.send_command(
        InstanceIds=[instancia], DocumentName="AWS-RunShellScript",
        Comment="FinCorp: hashes de las claves de la demo",
        Parameters={"commands": [comando_psql(sentencias)]},
    )["Command"]["CommandId"]
    for _ in range(30):
        time.sleep(2)
        try:
            res = ssm.get_command_invocation(CommandId=cmd, InstanceId=instancia)
        except ssm.exceptions.InvocationDoesNotExist:
            continue
        if res["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            break
    if res["Status"] != "Success" or res["StandardOutputContent"].count("INSERT 0 1") != len(claves):
        print(f"La base no acepto las claves ({res['Status']}): {res['StandardErrorContent'][:300]}",
              file=sys.stderr)
        return 1

    out.write_text(json.dumps(claves, indent=2) + "\n")
    out.chmod(0o600)
    print(f"[✓] {len(claves)} claves en {out} (a la base solo viajaron sus hashes)")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Crea las claves de API de la demo")
    p.add_argument("--out", type=Path, default=REPO_ROOT / ".demo_keys.json")
    p.add_argument("--ssm", action="store_true",
                   help="sin tunel: inserta los hashes por SSM Run Command (para la base en AWS)")
    p.add_argument("--region", default=os.getenv("AWS_REGION", "us-east-1"))
    args = p.parse_args()

    if args.ssm:
        return provision_via_ssm(args.out, args.region)

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
