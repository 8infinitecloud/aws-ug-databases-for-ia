#!/usr/bin/env python3
"""
ingest_from_curated.py — conecta el Lab 2 con el Lab 3 de verdad.

En el workshop la conexion entre labs es narrativa: el Lab 3 ingesta documentos
de compliance y *se cuenta* que los datos curados del Lab 2 alimentan a la IA,
pero tecnicamente no se tocan. Este script cierra ese hueco.

La parte interesante es COMO. Volcar 900 filas de transacciones a un vector store
no sirve de nada: nadie pregunta "dame la transaccion TXN0000042", y un embedding
de una fila de CSV no responde a ninguna pregunta de negocio. Lo que si sirve es
convertir los datos curados en **hechos agregados en lenguaje natural**, que es lo
que un asistente puede citar:

    "En 2026-09 FinCorp proceso 412 transacciones por 8.4M USD, de las cuales
     el 3% quedaron en estado FAILED."

Esa es la leccion: los datos curados se vuelven utiles para la IA por
**agregacion y narrativa**, no por volcado. El Lab 2 produce la tabla; este
script produce los hechos.

Uso:
    # 1. Generar los documentos derivados (no necesita el Lab 3 desplegado)
    python scripts/ingest_from_curated.py

    # 2. Verlos antes de ingestar
    ls labs/03-data-to-ai/data/derived/

    # 3. Ingestarlos al vector store (necesita el Lab 3 y su .env)
    cd labs/03-data-to-ai && python scripts/ingest_documents.py --source data/

    # O todo de una vez
    python scripts/ingest_from_curated.py --ingest

Requiere: pip install -r requirements.txt   (boto3, pandas, pyarrow)
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

try:
    import boto3
    import pandas as pd
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    sys.exit("Faltan dependencias. Ejecuta: pip install -r requirements.txt")

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO_ROOT / "labs/03-data-to-ai/data/derived"

GREEN, RED, YELLOW, BLUE, DIM, NC = (
    "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0;34m", "\033[2m", "\033[0m"
)


def info(msg: str) -> None:
    print(f"{BLUE}[INFO]{NC} {msg}")


def ok(msg: str) -> None:
    print(f"{GREEN}[✓]{NC} {msg}")


def warn(msg: str) -> None:
    print(f"{YELLOW}[⚠]{NC} {msg}")


def die(msg: str) -> None:
    sys.exit(f"{RED}[✗]{NC} {msg}")


# ── Localizar el bucket del Lab 1 ────────────────────────────────────────────
def discover_bucket() -> str:
    """Lee el bucket de los outputs del Lab 1, igual que hacen los scripts .sh."""
    try:
        proc = subprocess.run(
            ["terraform", f"-chdir={REPO_ROOT / 'infra/lab1'}", "output", "-raw", "s3_bucket_name"],
            capture_output=True, text=True, timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


# ── Leer lo que produjo el Lab 2 ─────────────────────────────────────────────
def read_curated(s3, bucket: str, prefix: str) -> pd.DataFrame:
    """Lee todos los Parquet de la zona curada y los concatena."""
    paginator = s3.get_paginator("list_objects_v2")
    keys = [
        obj["Key"]
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".parquet")
    ]
    if not keys:
        die(
            f"No hay Parquet en s3://{bucket}/{prefix}\n"
            f"      El Lab 2 no ha corrido todavia: "
            f"labs/02-transformation-quality-catalog/scripts/run_job.sh"
        )

    info(f"Leyendo {len(keys)} fichero(s) Parquet de s3://{bucket}/{prefix}")
    frames = []
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        frames.append(pd.read_parquet(io.BytesIO(body)))
    df = pd.concat(frames, ignore_index=True)
    ok(f"{len(df)} filas curadas leidas")
    return df


def read_quality_report(s3, bucket: str) -> dict:
    key = "quality-reports/latest/quality_report.json"
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError:
        warn(f"Sin quality report en s3://{bucket}/{key}")
        return {}
    return json.loads(body)


# ── Convertir datos en hechos que una IA pueda citar ─────────────────────────
def _money(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.2f}"


def doc_monthly_summary(df: pd.DataFrame) -> str:
    """Un hecho por mes y moneda: el nivel al que se hacen las preguntas de negocio."""
    lines = [
        "# Resumen mensual de transacciones",
        "",
        "Derivado de la zona curada del data lake (Lab 2). Cada cifra proviene de",
        "registros que pasaron las 8 reglas de calidad; los rechazados no se cuentan.",
        "",
    ]
    df = df.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    for (year, month), grupo in df.groupby(["year", "month"], sort=True):
        total = grupo["amount"].sum()
        lines.append(f"## Periodo {year}-{month}")
        lines.append("")
        lines.append(
            f"En {year}-{month} FinCorp proceso {len(grupo)} transacciones "
            f"por un total de {_money(total)} (suma de todas las monedas)."
        )
        lines.append("")

        por_moneda = grupo.groupby("currency")["amount"].agg(["count", "sum"])
        lines.append("Desglose por moneda:")
        for currency, fila in por_moneda.iterrows():
            lines.append(
                f"- {currency}: {int(fila['count'])} transacciones, "
                f"{_money(fila['sum'])} {currency}"
            )
        lines.append("")

        por_tipo = grupo["transaction_type"].value_counts()
        lines.append("Desglose por tipo de transaccion:")
        for tipo, n in por_tipo.items():
            lines.append(f"- {tipo}: {n} transacciones ({n / len(grupo):.1%})")
        lines.append("")

        por_estado = grupo["status"].value_counts()
        lines.append("Desglose por estado:")
        for estado, n in por_estado.items():
            lines.append(f"- {estado}: {n} transacciones ({n / len(grupo):.1%})")
        lines.append("")

    return "\n".join(lines)


def doc_quality_summary(report: dict, df: pd.DataFrame) -> str:
    """
    El reporte de calidad en prosa.

    Es el documento que permite al asistente responder algo que ninguna consulta
    SQL contesta bien: "¿puedo confiar en estos datos, y por que?".
    """
    lines = [
        "# Calidad de los datos de transacciones",
        "",
        "Derivado del reporte de calidad que genera el pipeline del Lab 2.",
        "",
    ]
    if not report:
        lines += [
            "No hay reporte de calidad disponible. Los datos curados existen, pero",
            "sin trazabilidad de que reglas se aplicaron.",
            "",
        ]
        return "\n".join(lines)

    resumen = report.get("summary", {})
    lines += [
        "## Situacion general",
        "",
        f"El pipeline leyo {resumen.get('total_rows_input', 'un numero no registrado de')} "
        f"filas del origen y acepto {resumen.get('total_rows_valid', '?')} "
        f"en la zona curada. Se rechazaron {resumen.get('total_rows_rejected', '?')} filas.",
        "",
        f"Reglas superadas: {resumen.get('rules_passed', '?')}. "
        f"Puntuacion global de calidad: {resumen.get('overall_dq_score', '?')}.",
        "",
        "Los registros rechazados **no se borran**: quedan en la zona `rejected/`",
        "del data lake con el motivo de rechazo, de modo que la decision es",
        "auditable y reversible.",
        "",
    ]

    comparacion = report.get("rules_raw_vs_curated", {})
    if comparacion:
        lines += ["## Que problemas traia el origen y como se resolvieron", ""]
        for nombre, datos in comparacion.items():
            resuelto = datos.get("resolved_by")
            if not resuelto:
                continue
            fallos = datos.get("raw_failed_rows", "?")
            if resuelto == "transformacion":
                lines.append(
                    f"- **{nombre}**: el origen traia {fallos} filas con este problema. "
                    f"Se resolvio normalizando el valor, sin perder la fila: el dato "
                    f"era recuperable."
                )
            elif resuelto == "rechazo":
                lines.append(
                    f"- **{nombre}**: el origen traia {fallos} filas afectadas. "
                    f"No se puede resolver transformando, porque implicaria decidir "
                    f"por el negocio. Esas filas se apartaron a `rejected/`."
                )
            else:
                lines.append(f"- **{nombre}**: {fallos} filas afectadas, sin resolver.")
        lines.append("")

    lines += [
        "## Implicaciones para quien consulta estos datos",
        "",
        "Las cifras de la zona curada describen unicamente transacciones que",
        "superaron las reglas de calidad. Para preguntas de conciliacion o de",
        "auditoria, donde importa el total del origen, hay que mirar tambien la",
        "zona `rejected/`.",
        "",
    ]
    return "\n".join(lines)


def doc_risk_profile(df: pd.DataFrame) -> str:
    """Hechos sobre concentracion: lo que un analista de compliance pregunta."""
    df = df.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    lines = [
        "# Perfil de actividad transaccional",
        "",
        "Derivado de la zona curada (Lab 2). Util para preguntas de compliance",
        "sobre concentracion de actividad y montos inusuales.",
        "",
        "## Montos",
        "",
        f"- Transaccion mas baja: {df['amount'].min():.2f}",
        f"- Transaccion mas alta: {df['amount'].max():.2f}",
        f"- Monto medio: {df['amount'].mean():.2f}",
        f"- Monto mediano: {df['amount'].median():.2f}",
        "",
    ]

    p99 = df["amount"].quantile(0.99)
    altas = df[df["amount"] >= p99]
    lines += [
        f"El percentil 99 de los montos esta en {p99:.2f}. Hay {len(altas)} "
        f"transacciones por encima de ese umbral, que son las que un proceso de "
        f"monitoreo revisaria primero.",
        "",
        "## Concentracion por cliente",
        "",
    ]
    por_cliente = df.groupby("customer_id")["amount"].agg(["count", "sum"]).sort_values("sum", ascending=False)
    lines.append(
        f"Los datos curados cubren {len(por_cliente)} clientes distintos. "
        f"Los 5 de mayor volumen acumulado:"
    )
    lines.append("")
    for customer_id, fila in por_cliente.head(5).iterrows():
        lines.append(
            f"- {customer_id}: {int(fila['count'])} transacciones, "
            f"{_money(fila['sum'])} acumulado"
        )
    lines.append("")

    if "branch_id" in df.columns:
        por_sucursal = df.groupby("branch_id")["amount"].agg(["count", "sum"]).sort_values("sum", ascending=False)
        lines += [
            "## Concentracion por sucursal",
            "",
            f"Actividad repartida en {len(por_sucursal)} sucursales. Las 5 de mayor volumen:",
            "",
        ]
        for branch_id, fila in por_sucursal.head(5).iterrows():
            lines.append(
                f"- {branch_id}: {int(fila['count'])} transacciones, "
                f"{_money(fila['sum'])} acumulado"
            )
        lines.append("")

    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--bucket", help="Bucket del data lake. Por defecto, lo lee del Lab 1.")
    parser.add_argument("--prefix", default="curated/transactions/", help="Prefijo de la zona curada")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="Directorio de salida")
    parser.add_argument("--region", default="us-east-1", help="Region AWS")
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="Tras generar, ingestar al vector store del Lab 3 (necesita su .env)",
    )
    args = parser.parse_args()

    print(f"\n{BLUE}{'=' * 62}{NC}")
    print(f"{BLUE}  Lab 2 → Lab 3: datos curados como hechos para la IA{NC}")
    print(f"{BLUE}{'=' * 62}{NC}\n")

    bucket = args.bucket or discover_bucket()
    if not bucket:
        die(
            "No se pudo determinar el bucket.\n"
            "      Pasalo con --bucket, o inicializa el Lab 1:\n"
            "      terraform -chdir=infra/lab1 init -backend-config=../backend.hcl"
        )
    info(f"Bucket: {bucket}")

    try:
        s3 = boto3.client("s3", region_name=args.region)
        df = read_curated(s3, bucket, args.prefix)
        report = read_quality_report(s3, bucket)
    except (ClientError, BotoCoreError) as exc:
        die(f"Error accediendo a S3: {str(exc)[:120]}")

    args.out.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cabecera = (
        "<!-- Generado por scripts/ingest_from_curated.py a partir de la zona\n"
        f"     curada del data lake. No editar a mano. Generado: {generado} -->\n\n"
    )

    documentos = {
        "transacciones-resumen-mensual.md": doc_monthly_summary(df),
        "transacciones-calidad-datos.md": doc_quality_summary(report, df),
        "transacciones-perfil-riesgo.md": doc_risk_profile(df),
    }

    print()
    for nombre, contenido in documentos.items():
        destino = args.out / nombre
        destino.write_text(cabecera + contenido, encoding="utf-8")
        ok(f"{destino.relative_to(REPO_ROOT)} ({len(contenido.splitlines())} lineas)")

    print(f"\n{DIM}Estos documentos son hechos agregados, no filas. Un vector store")
    print("con 900 transacciones sueltas no responde preguntas de negocio;")
    print(f"con estos tres documentos, si.{NC}")

    if args.ingest:
        print()
        info("Ingestando al vector store del Lab 3...")
        lab3 = REPO_ROOT / "labs/03-data-to-ai"
        proc = subprocess.run(
            [sys.executable, "scripts/ingest_documents.py", "--source", "data/"],
            cwd=lab3,
        )
        if proc.returncode != 0:
            die("La ingesta fallo. ¿Esta el .env del Lab 3 configurado?")
        ok("Documentos derivados ingestados")
    else:
        print("\n  Para ingestarlos:")
        print("    cd labs/03-data-to-ai && python scripts/ingest_documents.py --source data/")
        print("  O vuelve a ejecutar esto con --ingest\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
