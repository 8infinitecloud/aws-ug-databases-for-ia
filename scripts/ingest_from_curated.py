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
# En el repo, el Lab 3 vive en labs/03-data-to-ai. En el EC2 del Lab 3 este
# script viaja dentro de la app (deploy-app.sh lo copia a su scripts/), y el
# directorio de la app ES el Lab 3. Ahi se ejecuta en el cierre de la demo
# (demo_cierre.sh): dentro de la VPC, con alcance a Aurora sin tunel.
LAB3_DIR = (REPO_ROOT / "labs/03-data-to-ai"
            if (REPO_ROOT / "labs/03-data-to-ai").is_dir() else REPO_ROOT)
DEFAULT_OUT = LAB3_DIR / "data/derived"

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
        parte = pd.read_parquet(io.BytesIO(body))
        # year y month viven en la RUTA (curated/transactions/year=2026/month=10/),
        # no dentro del Parquet: el job de Glue las quita al escribir. Sin
        # recuperarlas de aqui, el resumen mensual fallaba contra S3 —en los
        # tests no se veia porque usan el DataFrame en memoria, que si las tiene.
        for trozo in key.split("/"):
            if "=" in trozo:
                columna, valor = trozo.split("=", 1)
                parte[columna] = valor
        frames.append(parte)
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


def read_telemetry(s3, bucket: str, prefix: str = "raw/telemetry/") -> pd.DataFrame:
    """
    La telemetria que aterriza Firehose: JSON por linea, comprimido con GZIP.

    Vacia si el Lab 4 no esta desplegado: el documento de salud de terminales
    lo dice en vez de fallar.
    """
    import gzip

    paginator = s3.get_paginator("list_objects_v2")
    keys = [o["Key"] for page in paginator.paginate(Bucket=bucket, Prefix=prefix)
            for o in page.get("Contents", [])]
    filas = []
    for key in keys:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        filas += [json.loads(linea) for linea in body.decode("utf-8").splitlines() if linea.strip()]
    if filas:
        ok(f"{len(filas)} latidos de telemetria leidos")
    else:
        warn(f"Sin telemetria en s3://{bucket}/{prefix} (¿Lab 4 desplegado?)")
    return pd.DataFrame(filas)


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
        "registros que pasaron las 10 reglas de calidad; los rechazados no se cuentan.",
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

        if "source" in grupo.columns:
            por_fuente = grupo["source"].value_counts()
            lines.append("Desglose por fuente (source):")
            for fuente, n in por_fuente.items():
                origen = ("el core del adquirente" if fuente == "acquiring"
                          else "la cartera legacy absorbida")
                lines.append(f"- {fuente}: {n} transacciones, de {origen}")
            lines.append("")

        por_canal = grupo["payment_method"].value_counts()
        lines.append("Desglose por canal de pago:")
        for canal, n in por_canal.items():
            lines.append(f"- {canal}: {n} operaciones ({n / len(grupo):.1%})")
        lines.append("")

        por_estado = grupo["status"].value_counts()
        lines.append("Desglose por estado:")
        for estado, n in por_estado.items():
            lines.append(f"- {estado}: {n} operaciones ({n / len(grupo):.1%})")
        lines.append("")

        # Lo que de verdad le importa a un adquirente: cuanto se cobro de
        # comision y cuanto se liquida al comercio.
        if "fee_amount" in grupo.columns:
            fees = pd.to_numeric(grupo["fee_amount"], errors="coerce").sum()
            nets = pd.to_numeric(grupo["net_amount"], errors="coerce").sum()
            if fees > 0:
                lines.append(
                    f"Comisiones cobradas en el periodo: {_money(fees)}. "
                    f"Liquidado a los comercios: {_money(nets)}."
                )
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

    por_fuente = report.get("by_source", {})
    if por_fuente:
        lines += ["## Calidad por fuente", ""]
        for fuente, d in por_fuente.items():
            if fuente == "acquiring":
                lines.append(
                    f"- **acquiring** (el core del adquirente): {d.get('rows')} filas, "
                    f"{d.get('rejected_rows')} rechazadas. "
                    + ("Ninguna regla fallaba en origen: la base de datos impide esos "
                       "errores con constraints." if not d.get("rules_failed_at_source")
                       else f"Reglas que fallaban en origen: {', '.join(d['rules_failed_at_source'])}. "
                            "Un rechazado del core es un fallo tecnico, no de datos.")
                )
            else:
                motivos = ", ".join(f"{k} ({v})" for k, v in d.get("rejection_reasons", {}).items())
                lines.append(
                    f"- **{fuente}** (la cartera absorbida): {d.get('rows')} filas, "
                    f"{d.get('rejected_rows')} rechazadas"
                    + (f" por: {motivos}." if motivos else ".")
                    + " Es la deuda del sistema antiguo, que no validaba."
                )
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


# Lo que significa cada codigo de ISO 8583 (DE 39) que produce el sistema, en
# las palabras con las que el corpus lo explica.
RESPONSE_CODES = {
    "00": "aprobada",
    "05": "no honrar (rechazo generico del emisor)",
    "51": "fondos insuficientes",
    "54": "tarjeta vencida",
    "91": "emisor no disponible",
}

CHANNEL_NAMES = {
    "CARD_PRESENT": "tarjeta presente en el terminal",
    "QR": "pago por QR desde el movil",
    "CARD_NOT_PRESENT": "pago online, sin terminal",
}

# La zona horaria de negocio. "Hoy" para un analista en Lima no es el dia UTC:
# una charla por la tarde cruza la medianoche UTC.
LOCAL_TZ = "America/Lima"


def doc_channels_and_issuer(df: pd.DataFrame) -> str:
    """
    Por canal: cuanto se aprueba y por que se rechaza.

    Es el documento que deja responder "¿por que fallan los pagos online?" con
    cifras, y no solo con la politica.
    """
    df = df.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    lines = [
        "# Canales de pago y respuestas del emisor",
        "",
        "Derivado de la zona curada (Lab 2). Una operacion por cobro; las",
        "declinadas tambien cuentan, porque un rechazo es un dato de negocio.",
        "",
    ]
    for canal, grupo in df.groupby("payment_method"):
        aprobadas = int((grupo["response_code"] == "00").sum())
        lines += [
            f"## Canal {canal}",
            "",
            f"El canal {canal} ({CHANNEL_NAMES.get(canal, canal)}) registro "
            f"{len(grupo)} operaciones, de las que se aprobaron {aprobadas} "
            f"({aprobadas / len(grupo):.1%}).",
            "",
        ]
        rechazos = grupo[grupo["response_code"] != "00"]["response_code"].value_counts()
        if len(rechazos):
            lines.append(f"Motivos de rechazo en {canal} (response_code):")
            for codigo, n in rechazos.items():
                lines.append(f"- {codigo} ({RESPONSE_CODES.get(codigo, 'otro')}): {n} operaciones")
            lines.append("")

    online = df[df["payment_method"] == "CARD_NOT_PRESENT"]
    if len(online):
        por_comercio = (online.assign(rechazada=online["response_code"] != "00")
                        .groupby(["customer_id", "merchant_name"])["rechazada"]
                        .agg(["count", "mean"]).sort_values("mean", ascending=False))
        lines += [
            "## Comercios online con mas rechazos",
            "",
            "Un ratio de rechazos alto en una tienda online es la firma del card",
            "testing (ver la Politica de Pagos Online).",
            "",
        ]
        for (cid, nombre), fila in por_comercio.head(5).iterrows():
            lines.append(f"- {nombre} ({cid}): {int(fila['count'])} operaciones online, "
                         f"{fila['mean']:.1%} rechazadas")
        lines.append("")
    return "\n".join(lines)


def doc_terminal_health(telemetry: pd.DataFrame, df: pd.DataFrame,
                        now: datetime | None = None) -> str:
    """
    La salud de la flota, en hechos: senal, timeouts, bateria y ultimo latido.

    Con el runbook de terminales, permite responder "¿por que este comercio
    tiene tantos cobros fallidos?" con lo que realmente paso en el terminal.
    """
    lines = [
        "# Salud de los terminales",
        "",
        "Derivado de la telemetria que publican los terminales (raw/telemetry/)",
        "y de los cobros de la zona curada. Ver el Runbook de Operacion de",
        "Terminales para interpretar cada cifra.",
        "",
    ]
    if telemetry.empty:
        lines += ["No hay telemetria disponible: ningun terminal ha publicado latidos.", ""]
        return "\n".join(lines)

    now = now or datetime.now(timezone.utc)
    t = telemetry.copy()
    for col in ("rssi_dbm", "http_timeouts", "http_requests", "battery_pct",
                "received_at_ms", "qr_shown", "qr_paid"):
        if col in t.columns:
            t[col] = pd.to_numeric(t[col], errors="coerce")
    nombres = {}
    if "terminal_id" in df.columns:
        nombres = (df.dropna(subset=["terminal_id"])
                   .drop_duplicates("terminal_id").set_index("terminal_id")["merchant_name"].to_dict())

    for tid, g in t.groupby("terminal_id"):
        ultimo = pd.to_datetime(g["received_at_ms"].max(), unit="ms", utc=True)
        minutos = (now - ultimo.to_pydatetime()).total_seconds() / 60
        senal = g["rssi_dbm"].mean()
        timeouts = int(g["http_timeouts"].sum())
        peticiones = int(g["http_requests"].sum())
        bateria = g.sort_values("received_at_ms")["battery_pct"].iloc[-1]
        comercio = nombres.get(tid, "comercio desconocido")

        lines.append(f"## Terminal {tid} ({comercio})")
        lines.append("")
        frase = (f"El terminal {tid} de {comercio} tuvo una senal media de {senal:.0f} dBm "
                 f"y {timeouts} timeouts en {peticiones} llamadas a la API.")
        if senal < -80:
            frase += " Senal critica: los timeouts son esperables y apuntan a la red del comercio, no al emisor."
        elif senal < -75:
            frase += " Senal debil."
        lines.append(frase)
        estado = (f"Su ultimo latido fue hace {minutos:.0f} minutos, con la bateria al {bateria:.0f}%.")
        if minutos > 2:
            estado += " Lleva mas de 2 minutos sin latido: se considera offline."
            if bateria <= 15:
                estado += " Con la bateria tan baja, lo mas probable es que se haya quedado sin carga."
        lines.append(estado)
        if "qr_shown" in g.columns and g["qr_shown"].sum() > 0:
            conv = g["qr_paid"].sum() / g["qr_shown"].sum()
            lines.append(f"De {int(g['qr_shown'].sum())} QR mostrados se pagaron "
                         f"{int(g['qr_paid'].sum())} (conversion {conv:.0%}).")
        lines.append("")
    return "\n".join(lines)


# Cuantos pagos de hoy se listan uno a uno en operaciones-actividad-hoy.md.
DETALLE_HOY = 15


def doc_today(df: pd.DataFrame, now: datetime | None = None) -> str:
    """
    Lo que paso HOY, en hora de Lima.

    Es el documento del cierre en vivo: los pagos que hace la audiencia llegan
    por el CDC del Lab 1, Glue los cura, y este documento los convierte en una
    frase que el asistente puede citar.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(LOCAL_TZ)
    now = (now or datetime.now(timezone.utc)).astimezone(tz)
    hoy = now.date()
    df = df.copy()
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    # format="ISO8601", no la deteccion automatica: pandas 2 deduce UN formato
    # de la primera fila y convierte en NaT las que no encajan. Las fechas del
    # core traen microsegundos y las del legacy no, asi que segun que fuente
    # saliera primero desaparecian los pagos del core, que son los de la sala.
    momento = pd.to_datetime(df.get("created_at"), errors="coerce", utc=True, format="ISO8601")
    df["fecha_local"] = momento.dt.tz_convert(tz).dt.date
    del_dia = df[df["fecha_local"] == hoy]

    lines = [
        f"# Actividad de hoy, {hoy.isoformat()}",
        "",
        f"Pagos procesados hoy ({hoy.isoformat()}, hora de Lima), segun la zona",
        f"curada actualizada a las {now.strftime('%H:%M')}. Los pagos llegan desde el",
        "core por replicacion continua (CDC), asi que esta cifra incluye los",
        "pagos de los ultimos minutos.",
        "",
    ]
    if del_dia.empty:
        lines += [f"Hoy, {hoy.isoformat()}, todavia no se ha procesado ningun pago.", ""]
        return "\n".join(lines)

    aprob = del_dia[del_dia["response_code"] == "00"]
    n = len(del_dia)
    lines += [
        f"Hoy se {'proceso' if n == 1 else 'procesaron'} {n} {'pago' if n == 1 else 'pagos'}, "
        f"de los que se {'aprobo' if len(aprob) == 1 else 'aprobaron'} {len(aprob)}: "
        f"una tasa de aprobacion del {len(aprob) / n:.1%}.",
        "",
        "Por canal:",
    ]
    for canal, n in del_dia["payment_method"].value_counts().items():
        lines.append(f"- {canal}: {n} pagos")
    lines += ["", "Por moneda (importe aprobado):"]
    for moneda, total in aprob.groupby("currency")["amount"].sum().items():
        lines.append(f"- {moneda}: {total:.2f}")
    lines += ["", "Comercios con mas pagos hoy:"]
    for nombre, n in del_dia["merchant_name"].value_counts().head(5).items():
        lines.append(f"- {nombre}: {n} pagos")

    # El detalle, pago a pago. Sin el, "¿a que hora fue?" o "¿por cuanto?" no
    # tienen respuesta: los agregados de arriba no la llevan. Solo los ultimos
    # DETALLE_HOY, para que el documento no crezca con el dia, y cada linea se
    # explica sola (fecha, hora, importe, resultado) porque al trocear el
    # documento puede quedar en un chunk sin el titulo. Nada de la tarjeta ni
    # del comprador: hora, importe, resultado, comercio y canal.
    del_dia = del_dia.assign(hora=momento[del_dia.index].dt.tz_convert(tz))
    ultimos = del_dia.sort_values("hora", ascending=False).head(DETALLE_HOY)
    titulo = (f"Detalle de los {len(ultimos)} pagos mas recientes de hoy"
              if len(ultimos) < len(del_dia) else "Detalle de todos los pagos de hoy")
    lines += ["", f"## {titulo} ({hoy.isoformat()}, hora de Lima)", ""]
    for _, pago in ultimos.iterrows():
        codigo = str(pago.get("response_code") or "")
        resultado = ("aprobado" if codigo == "00"
                     else f"rechazado con codigo {codigo} "
                          f"({RESPONSE_CODES.get(codigo, 'otro motivo')})")
        lines.append(
            f"- Pago de hoy {hoy.isoformat()} a las {pago['hora'].strftime('%H:%M')} "
            f"(hora de Lima): {pago['amount']:.2f} {pago['currency']}, {resultado}, "
            f"comercio {pago['merchant_name']}, canal {pago['payment_method']}."
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
        telemetry = read_telemetry(s3, bucket)
    except (ClientError, BotoCoreError) as exc:
        die(f"Error accediendo a S3: {str(exc)[:120]}")

    args.out.mkdir(parents=True, exist_ok=True)
    generado = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cabecera = (
        "<!-- Generado por scripts/ingest_from_curated.py a partir de la zona\n"
        f"     curada del data lake. No editar a mano. Generado: {generado} -->\n\n"
    )

    documentos = {
        "operaciones-resumen-mensual.md": doc_monthly_summary(df),
        "operaciones-calidad-datos.md": doc_quality_summary(report, df),
        "operaciones-perfil-riesgo.md": doc_risk_profile(df),
        "operaciones-canales-emisor.md": doc_channels_and_issuer(df),
        "operaciones-salud-terminales.md": doc_terminal_health(telemetry, df),
        "operaciones-actividad-hoy.md": doc_today(df),
    }

    print()
    for nombre, contenido in documentos.items():
        destino = args.out / nombre
        destino.write_text(cabecera + contenido, encoding="utf-8")
        ok(f"{destino.relative_to(REPO_ROOT)} ({len(contenido.splitlines())} lineas)")

    print(f"\n{DIM}Estos documentos son hechos agregados, no filas. Un vector store")
    print("con miles de transacciones sueltas no responde preguntas de negocio;")
    print(f"con estos {len(documentos)} documentos, si.{NC}")

    if args.ingest:
        print()
        info("Ingestando al vector store del Lab 3...")
        lab3 = LAB3_DIR
        # Solo los derivados: el resto del corpus no cambio. Reingestar un
        # documento SUSTITUYE sus chunks (ingestion/store.py), asi que la cifra
        # de hace diez minutos no convive con la nueva.
        proc = subprocess.run(
            [sys.executable, "scripts/ingest_documents.py",
             "--source", str(args.out.resolve())],
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
