"""
glue_job.py — Lab 2: Transformation + Data Quality + Catalog
=============================================================
AWS Glue Python Shell job (Python 3.x, 1/16 DPU).

Pipeline:
  1. Lee CSV desde S3 raw/transactions/ (output del Lab 1 vía DMS)
  2. Transforma: tipos, fechas, normalización
  3. Aplica Data Quality rules (Python-native, en paralelo al ruleset DQDL del CFN)
  4. Separa registros válidos de rechazados
  5. Escribe curated/ en formato Parquet particionado por year/month
  6. Escribe rejected/ con los registros que no pasaron DQ
  7. Genera quality_report.json en s3://bucket/quality-reports/

Por qué Python Shell (no PySpark):
  - Inicia en ~20 segundos vs ~3 minutos de PySpark
  - Dataset ~1000 filas — pandas es suficiente
  - Más legible para la audiencia en vivo

Uso manual (desarrollo local):
  python glue_job.py --S3_BUCKET mi-bucket --RAW_PREFIX raw/ --CURATED_PREFIX curated/

En Glue (vía CloudFormation):
  Los parámetros se pasan como DefaultArguments en el job definition.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from io import BytesIO, StringIO
from typing import Any

import boto3
import pandas as pd

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Constantes del dominio de adquirencia ─────────────────────────────────────
# Somos el adquirente: procesamos autorizaciones de pago para comercios
# afiliados. No hay depositos ni retiros; hay capturas, rechazos y devoluciones.
VALID_PAYMENT_METHODS = {"CARD_PRESENT", "QR"}
VALID_STATUSES        = {"CAPTURED", "FAILED", "REFUNDED", "REVERSED"}
VALID_CURRENCIES      = {"PEN", "USD", "EUR"}

# ISO 8583 DE 39: solo el 00 aprueba.
RESPONSE_CODE_APPROVED = "00"

# Codigos que escribia el core antiguo. Dos generaciones de sistema nombrando lo
# mismo distinto: el caso mas comun de dato recuperable por normalizacion.
METHOD_NORMALIZATION = {
    "TARJETA":      "CARD_PRESENT",
    "POS":          "CARD_PRESENT",
    "CARD":         "CARD_PRESENT",
    "CODIGO_QR":    "QR",
    "CÓDIGO_QR":    "QR",
    "QR_CODE":      "QR",
}

# Igual con los estados: el core antiguo escribia en espanol.
STATUS_NORMALIZATION = {
    "CAPTURADA":  "CAPTURED",
    "RECHAZADA":  "FAILED",
    "DEVUELTA":   "REFUNDED",
    "REVERSADA":  "REVERSED",
}

# Tolerancia al comparar importes. Los CSV traen 2 decimales, asi que un centimo
# de diferencia es redondeo y no un descuadre real.
LEDGER_TOLERANCE = 0.01

# ── Argumentos del job ────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """
    En Glue Python Shell los argumentos se pasan con prefijo '--'.
    En desarrollo local también funciona igual.
    """
    parser = argparse.ArgumentParser(description="FinCorp Glue ETL Job — Lab 2")
    parser.add_argument("--S3_BUCKET",       required=True,              help="Nombre del bucket S3")
    parser.add_argument("--RAW_PREFIX",      default="raw/",             help="Prefijo de la zona raw")
    parser.add_argument("--SOURCE_PATH",     default=DEFAULT_SOURCE_PATH,
                        help="Ruta de la tabla dentro de raw/, como la escribe DMS")
    parser.add_argument("--CURATED_PREFIX",  default="curated/",         help="Prefijo de la zona curated")
    parser.add_argument("--REJECTED_PREFIX", default="rejected/",        help="Prefijo para registros rechazados")
    parser.add_argument("--REPORTS_PREFIX",  default="quality-reports/", help="Prefijo para reportes de calidad")
    parser.add_argument("--DATABASE_NAME",   default="financial_platform",help="Nombre de la DB en Glue Catalog")
    parser.add_argument("--JOB_NAME",        default="fincorp-glue-etl",  help="Nombre del Glue job (para logs)")
    # parse_known_args y no parse_args: Glue inyecta sus propios argumentos al
    # invocar el script —--enable-metrics, --scriptLocation, --python-version y
    # los que anada en el futuro— y argparse en modo estricto aborta con exit 2
    # antes de ejecutar una sola linea del job. Con esto se ignoran los que no
    # son nuestros.
    args, ajenos = parser.parse_known_args()
    if ajenos:
        log.info("argumentos de Glue ignorados: %s", " ".join(ajenos))
    return args


# ── Helpers S3 ────────────────────────────────────────────────────────────────

def list_s3_files(s3_client, bucket: str, prefix: str, extension: str = ".csv") -> list[str]:
    """Lista objetos en S3 con un prefijo dado y extensión."""
    keys = []
    paginator = s3_client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            if obj["Key"].endswith(extension):
                keys.append(obj["Key"])
    return keys


def read_csv_from_s3(s3_client, bucket: str, key: str) -> pd.DataFrame:
    """Lee un CSV desde S3 y retorna un DataFrame."""
    response = s3_client.get_object(Bucket=bucket, Key=key)
    content  = response["Body"].read().decode("utf-8")
    df = pd.read_csv(StringIO(content), dtype=str, keep_default_na=False)
    # Reemplazar cadenas vacías con NaN para que pd.isnull() funcione correctamente
    df = df.replace("", pd.NA)
    return df


def write_parquet_to_s3(s3_client, df: pd.DataFrame, bucket: str, key: str) -> None:
    """Escribe un DataFrame como Parquet en S3."""
    buffer = BytesIO()
    df.to_parquet(buffer, index=False, engine="pyarrow")
    buffer.seek(0)
    s3_client.put_object(Bucket=bucket, Key=key, Body=buffer.getvalue())
    log.info(f"  → Parquet escrito: s3://{bucket}/{key} ({len(df)} filas)")


def write_json_to_s3(s3_client, data: dict, bucket: str, key: str) -> None:
    """Escribe un dict como JSON en S3."""
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(data, indent=2, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    log.info(f"  → JSON escrito: s3://{bucket}/{key}")


# ── PASO 1: Leer datos raw ────────────────────────────────────────────────────

# DMS no deja los ficheros donde uno esperaria. Su convencion es
#     <bucket_folder>/<esquema>/<tabla>/LOAD*.csv
# asi que con bucket_folder=raw y la tabla del Lab 1 sale
#     raw/acquiring/tbl_transactions_for_ingestion/
# y no `raw/transactions/`, que es lo que este job leia antes. Los dos labs eran
# correctos por separado y nunca se habian ejecutado juntos: el job no encontraba
# nada y fallaba con un FileNotFoundError que culpaba al Lab 1.
DEFAULT_SOURCE_PATH = "acquiring/tbl_transactions_for_ingestion/"


def read_raw_data(s3_client, bucket: str, raw_prefix: str,
                  source_path: str = DEFAULT_SOURCE_PATH) -> tuple[pd.DataFrame, dict]:
    """Lee los CSV que dejo DMS en la zona raw y los concatena."""
    prefix = f"{raw_prefix}{source_path}"
    keys = list_s3_files(s3_client, bucket, prefix)

    if not keys:
        # Compatibilidad con datasets subidos a mano y con el runner local.
        for alternativa in (f"{raw_prefix}transactions/", "staging/transactions/"):
            keys = list_s3_files(s3_client, bucket, alternativa)
            if keys:
                log.warning("No habia CSVs en %s; usando %s", prefix, alternativa)
                prefix = alternativa
                break

    if not keys:
        raise FileNotFoundError(
            f"No se encontraron CSV en s3://{bucket}/{prefix}. "
            f"Comprueba que la tarea DMS del Lab 1 termino con 'Table completed' "
            f"y que escribio en esa ruta: DMS usa <bucket_folder>/<esquema>/<tabla>/."
        )

    frames = []
    for key in keys:
        log.info(f"Leyendo: s3://{bucket}/{key}")
        df = read_csv_from_s3(s3_client, bucket, key)
        frames.append(df)
        log.info(f"  → {len(df)} filas, columnas: {list(df.columns)}")

    raw_df = pd.concat(frames, ignore_index=True)

    metadata = {
        "source_files":      keys,
        "total_rows_raw":    len(raw_df),
        "columns":           list(raw_df.columns),
        "read_at":           datetime.now(timezone.utc).isoformat(),
    }
    log.info(f"Total cargado: {len(raw_df)} filas desde {len(keys)} archivo(s)")
    return raw_df, metadata


# ── PASO 2: Transformaciones ──────────────────────────────────────────────────

def normalize_transaction_date(date_str: Any) -> str | None:
    """
    Convierte fechas al formato estándar YYYY-MM-DD.
    Acepta: YYYY-MM-DD (ya correcto) y DD/MM/YYYY (formato incorrecto del dataset).
    Retorna None si el formato no es reconocible.
    """
    if pd.isna(date_str) or not isinstance(date_str, str):
        return None

    date_str = date_str.strip()

    # Formato correcto: YYYY-MM-DD
    if len(date_str) == 10 and date_str[4] == "-":
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return date_str
        except ValueError:
            return None

    # Formato incorrecto del dataset: DD/MM/YYYY
    if len(date_str) == 10 and date_str[2] == "/":
        try:
            dt = datetime.strptime(date_str, "%d/%m/%Y")
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None

    return None


def transform_dataframe(raw_df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """
    Aplica todas las transformaciones al DataFrame raw.

    Transformaciones:
      a. Normalizar transaction_date (DD/MM/YYYY → YYYY-MM-DD)
      b. Normalizar payment_method (codigos del core antiguo)
      c. Normalizar status (upper case)
      d. Convertir amount a numérico
      e. Agregar columnas de auditoría y particionado
    """
    df = raw_df.copy()
    stats: dict[str, Any] = {}

    # a. Normalizar transaction_date
    original_dates = df["transaction_date"].copy()
    df["transaction_date_normalized"] = df["transaction_date"].apply(normalize_transaction_date)

    bad_dates = df["transaction_date_normalized"].isna() & df["transaction_date"].notna()
    stats["dates_fixed_format"] = int(
        (original_dates.str.contains("/", na=False)).sum()
    )
    stats["dates_unrecognized"] = int(bad_dates.sum())
    df["transaction_date"] = df["transaction_date_normalized"].combine_first(df["transaction_date"])
    df.drop(columns=["transaction_date_normalized"], inplace=True)
    log.info(f"  Fechas: {stats['dates_fixed_format']} convertidas DD/MM/YYYY→YYYY-MM-DD, "
             f"{stats['dates_unrecognized']} no reconocidas")

    # b. Normalizar payment_method (upper + codigos del core antiguo)
    original_methods = df["payment_method"].copy()
    df["payment_method"] = df["payment_method"].str.strip().str.upper()
    df["payment_method"] = df["payment_method"].replace(METHOD_NORMALIZATION)
    stats["methods_normalized"] = int(
        (original_methods.str.upper().isin(set(METHOD_NORMALIZATION.keys()))).sum()
    )
    log.info(f"  Metodos de pago normalizados: {stats['methods_normalized']}")

    # c. Normalizar status (upper + codigos en espanol del core antiguo)
    original_status = df["status"].copy()
    df["status"] = df["status"].str.strip().str.upper()
    df["status"] = df["status"].replace(STATUS_NORMALIZATION)
    stats["status_normalized"] = int((original_status != df["status"]).sum())
    log.info(f"  Status normalizados: {stats['status_normalized']}")

    # c2. Convertir los importes economicos. Sin esto, las reglas de libro mayor
    # compararian cadenas y no numeros.
    for col in ("fee_amount", "net_amount"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # d. Convertir amount a numérico (NULL/vacío → NaN)
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    stats["amount_null_count"] = int(df["amount"].isna().sum())
    log.info(f"  Amount NULL: {stats['amount_null_count']} registros")

    # e. Columnas de auditoría y particionado
    df["processed_at"] = datetime.now(timezone.utc).isoformat()
    # Extraer year/month del transaction_date normalizado para particionado
    df["year"]  = pd.to_datetime(df["transaction_date"], errors="coerce").dt.year.astype("Int64").astype(str)
    df["month"] = pd.to_datetime(df["transaction_date"], errors="coerce").dt.month.apply(
        lambda x: str(x).zfill(2) if pd.notna(x) else "00"
    )

    stats["total_rows_after_transform"] = len(df)
    return df, stats


# ── PASO 3: Data Quality ──────────────────────────────────────────────────────

def measure_rules(df: pd.DataFrame) -> dict:
    """
    Mide las 10 reglas del ruleset DQDL sobre CUALQUIER dataframe, sin modificarlo.

    Ocho son validaciones de columna (completitud, unicidad, valores validos,
    longitud). Las dos ultimas comparan CAMPOS ENTRE SI, y son las propias de un
    adquirente: que el libro mayor cuadre y que una operacion capturada tenga la
    aprobacion del emisor. Ninguna herramienta que mire columna a columna las ve.

    Se aplica dos veces en el job: una sobre los datos raw y otra sobre los
    curados. Esa comparacion es el punto del lab — muestra que problemas traia
    el origen y cuales quedaron resueltos. Medir solo al final dejaria las
    reglas de payment_method y status siempre en PASS, porque la
    transformacion ya las habia normalizado, y no se veria nada.

    Devuelve solo metricas: la decision de aceptar o rechazar filas vive en
    apply_data_quality.
    """
    total = len(df)
    if total == 0:
        return {"rules": {}, "rules_passed": 0, "rules_failed": 0, "overall_score": 1.0}

    # Un importe que no se puede convertir a numero es tan inservible como uno
    # ausente, asi que cuenta igual para la completitud.
    null_amount    = pd.to_numeric(df["amount"], errors="coerce").isna()
    null_txn_id    = df["transaction_id"].isna()
    null_cust_id   = df["customer_id"].isna()
    invalid_method = ~df["payment_method"].isin(VALID_PAYMENT_METHODS)
    invalid_status = ~df["status"].isin(VALID_STATUSES)
    invalid_curr   = ~df["currency"].isin(VALID_CURRENCIES)

    # ── Reglas de REGLA DE NEGOCIO, propias de la adquirencia ────────────────
    # Estas dos no son validaciones de columna: comparan varios campos entre si.
    # Ninguna herramienta que mire "esta columna tiene nulos" las detecta, y son
    # las que de verdad importan en un sistema de pagos.

    # El libro mayor: neto = importe - comision. Si no cuadra, la liquidacion al
    # comercio sale mal, y eso es dinero real mal pagado.
    #
    # Se convierte a numerico AQUI, en copias locales, por dos razones: esta
    # funcion se aplica tambien sobre los datos raw, donde los importes siguen
    # siendo cadenas del CSV; y la funcion promete no modificar el dataframe que
    # recibe.
    amount_num = pd.to_numeric(df["amount"], errors="coerce")
    fee_num    = pd.to_numeric(df.get("fee_amount"), errors="coerce")
    net_num    = pd.to_numeric(df.get("net_amount"), errors="coerce")

    has_ledger    = amount_num.notna() & fee_num.notna() & net_num.notna()
    ledger_diff   = (amount_num - fee_num - net_num).abs()
    ledger_broken = has_ledger & (ledger_diff > LEDGER_TOLERANCE)

    # Contradiccion interna: la operacion dice estar capturada y el emisor dice
    # que no la aprobo. Una de las dos afirmaciones es falsa, y cobrar sin
    # autorizacion del emisor acaba en contracargo.
    claims_captured = df["status"] == "CAPTURED"
    issuer_declined = df["response_code"] != RESPONSE_CODE_APPROVED
    captured_without_auth = claims_captured & issuer_declined

    # ColumnLength del DQDL: entre 1 y 20 caracteres.
    txn_id_len  = df["transaction_id"].astype(str).str.len()
    bad_txn_len = (txn_id_len < 1) | (txn_id_len > 20) | null_txn_id

    def rule(ok_mask, threshold: float, extra: dict | None = None) -> dict:
        score = float(ok_mask.sum()) / total
        out = {
            "score":       round(score, 4),
            "threshold":   threshold,
            "passed":      score >= threshold,
            "failed_rows": int((~ok_mask).sum()),
        }
        if extra:
            out.update(extra)
        return out

    uniqueness = float(df["transaction_id"].nunique()) / total

    rules = {
        # R01 — Completeness "amount" >= 0.95
        "completeness_amount": rule(~null_amount, 0.95),
        # R02 — IsComplete "transaction_id"
        "completeness_transaction_id": rule(~null_txn_id, 1.0),
        # R03 — IsComplete "customer_id"
        "completeness_customer_id": rule(~null_cust_id, 1.0),
        # R04 — Uniqueness "transaction_id" = 1.0
        "uniqueness_transaction_id": {
            "score":       round(uniqueness, 4),
            "threshold":   1.0,
            "passed":      uniqueness >= 1.0,
            "failed_rows": int(df.duplicated(subset=["transaction_id"], keep=False).sum()),
        },
        # R05 — ColumnValues "payment_method"
        "validity_payment_method": rule(
            ~invalid_method, 1.0,
            {"invalid_values": df[invalid_method]["payment_method"].value_counts().to_dict()},
        ),
        # R06 — ColumnValues "status"
        "validity_status": rule(
            ~invalid_status, 1.0,
            {"invalid_values": df[invalid_status]["status"].value_counts().to_dict()},
        ),
        # R07 — ColumnValues "currency"
        "validity_currency": rule(~invalid_curr, 1.0),
        # R08 — ColumnLength "transaction_id" between 1 and 40
        "length_transaction_id": rule(~bad_txn_len, 1.0),
        # R09 — CustomSql: net_amount = amount - fee_amount
        # La invariante contable del adquirente. Se mide solo sobre las filas que
        # tienen los tres importes: una fila sin comision no esta descuadrada,
        # simplemente no tiene libro todavia.
        "ledger_coherence": {
            "score":       round(float((~ledger_broken).sum()) / total, 4),
            "threshold":   1.0,
            "passed":      bool(~ledger_broken.any()),
            "failed_rows": int(ledger_broken.sum()),
            "checked_rows": int(has_ledger.sum()),
        },
        # R10 — CustomSql: status CAPTURED implica response_code = 00
        "captured_implies_approved": {
            "score":       round(float((~captured_without_auth).sum()) / total, 4),
            "threshold":   1.0,
            "passed":      bool(~captured_without_auth.any()),
            "failed_rows": int(captured_without_auth.sum()),
        },
    }

    passed = sum(1 for r in rules.values() if r["passed"])
    return {
        "rules":         rules,
        "rules_passed":  passed,
        "rules_failed":  len(rules) - passed,
        "overall_score": round(passed / len(rules), 4),
    }


def apply_data_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Evalúa las reglas de calidad y separa registros válidos de rechazados.

    Un registro se rechaza si falla cualquiera de las reglas del ruleset DQDL,
    incluidas las dos de regla de negocio: un libro descuadrado liquidaria mal al
    comercio, y una captura sin aprobacion del emisor acaba en contracargo. Las
    dos exigen revision manual, asi que se apartan en vez de pasar a curated.

    Las metricas las calcula measure_rules sobre el mismo dataframe.
    """
    total = len(df)

    # Crear máscara de rechazo por cada regla
    mask_null_amount    = df["amount"].isna()
    mask_null_txn_id    = df["transaction_id"].isna()
    mask_null_cust_id   = df["customer_id"].isna()
    mask_invalid_method = ~df["payment_method"].isin(VALID_PAYMENT_METHODS)
    mask_invalid_status = ~df["status"].isin(VALID_STATUSES)
    mask_invalid_curr   = ~df["currency"].isin(VALID_CURRENCIES)

    # Duplicados: marcar TODAS las ocurrencias del ID duplicado como rechazadas
    dup_mask            = df.duplicated(subset=["transaction_id"], keep=False)

    # ── Reglas de negocio: tambien se rechazan, y por razones de dinero ──────
    # Una fila cuyo libro no cuadra liquidaria al comercio un importe equivocado.
    # Una capturada sin aprobacion del emisor es un contracargo esperando. Las dos
    # exigen revision manual antes de liquidar, asi que van a cuarentena en vez de
    # pasar a curated.
    _amount = pd.to_numeric(df["amount"], errors="coerce")
    _fee    = pd.to_numeric(df.get("fee_amount"), errors="coerce")
    _net    = pd.to_numeric(df.get("net_amount"), errors="coerce")
    mask_ledger_broken = (
        _amount.notna() & _fee.notna() & _net.notna()
        & ((_amount - _fee - _net).abs() > LEDGER_TOLERANCE)
    )
    mask_captured_no_auth = (df["status"] == "CAPTURED") & (
        df["response_code"] != RESPONSE_CODE_APPROVED
    )

    # Máscara combinada: rechazar si falla ALGUNA regla
    reject_mask = (
        mask_null_amount    |
        mask_null_txn_id    |
        mask_null_cust_id   |
        mask_invalid_method |
        mask_invalid_status |
        mask_invalid_curr   |
        dup_mask            |
        mask_ledger_broken  |
        mask_captured_no_auth
    )

    valid_df    = df[~reject_mask].copy()
    rejected_df = df[reject_mask].copy()

    # Agregar columna con razón(es) de rechazo
    reasons = pd.Series([""] * total, index=df.index)
    reasons[mask_null_amount]    += "NULL_AMOUNT;"
    reasons[mask_null_txn_id]    += "NULL_TXN_ID;"
    reasons[mask_null_cust_id]   += "NULL_CUST_ID;"
    reasons[mask_invalid_method] += "INVALID_METHOD;"
    reasons[mask_invalid_status] += "INVALID_STATUS;"
    reasons[mask_invalid_curr]   += "INVALID_CURRENCY;"
    reasons[dup_mask]             += "DUPLICATE_TXN_ID;"
    reasons[mask_ledger_broken]   += "LEDGER_MISMATCH;"
    reasons[mask_captured_no_auth] += "CAPTURED_WITHOUT_AUTH;"
    rejected_df["rejection_reason"] = reasons[reject_mask].values

    # Metricas sobre valid_df, no sobre df: lo que describe la calidad de la
    # zona curated es lo que acaba escrito ahi, no lo que entro. Midiendo df
    # (antes de apartar los rechazados) uniqueness seguia saliendo en FAIL
    # aunque los duplicados ya no llegaran a curated.
    dq_metrics = measure_rules(valid_df)
    dq_metrics.update({
        "total_rows":      total,
        "valid_rows":      len(valid_df),
        "rejected_rows":   len(rejected_df),
        "acceptance_rate": round(len(valid_df) / total, 4) if total > 0 else 1.0,
    })

    log.info(f"  Data Quality: {len(valid_df)} válidos, {len(rejected_df)} rechazados")
    log.info(f"  Score general: {dq_metrics['overall_score']:.1%}")
    log.info(f"  Reglas pasadas: {dq_metrics['rules_passed']}/{len(dq_metrics['rules'])}")

    return valid_df, rejected_df, dq_metrics


# ── PASO 4: Escribir curated Parquet ─────────────────────────────────────────

def write_curated_data(
    s3_client,
    valid_df: pd.DataFrame,
    bucket: str,
    curated_prefix: str,
) -> list[str]:
    """
    Escribe los registros válidos en formato Parquet particionado por year/month.
    Ej: curated/transactions/year=2026/month=09/part-00000.parquet
    """
    written_keys = []

    if valid_df.empty:
        log.warning("No hay registros válidos para escribir en curated/")
        return written_keys

    # Agrupar por year/month para crear particiones
    for (year, month), group_df in valid_df.groupby(["year", "month"]):
        # Eliminar columnas de particionado del Parquet (están en la ruta)
        parquet_df = group_df.drop(columns=["year", "month"], errors="ignore")
        key = f"{curated_prefix}transactions/year={year}/month={month}/part-00000.parquet"
        write_parquet_to_s3(s3_client, parquet_df, bucket, key)
        written_keys.append(key)

    log.info(f"Curated: {len(written_keys)} partición(es) escritas")
    return written_keys


# ── PASO 5: Escribir registros rechazados ─────────────────────────────────────

def write_rejected_data(
    s3_client,
    rejected_df: pd.DataFrame,
    bucket: str,
    rejected_prefix: str,
    run_timestamp: str,
) -> str | None:
    """Escribe registros rechazados como CSV para auditoría y revisión."""
    if rejected_df.empty:
        log.info("Sin registros rechazados (dataset perfectamente limpio).")
        return None

    key = f"{rejected_prefix}transactions/{run_timestamp}/rejected.csv"
    csv_buffer = StringIO()
    rejected_df.to_csv(csv_buffer, index=False)
    s3_client.put_object(
        Bucket=bucket,
        Key=key,
        Body=csv_buffer.getvalue().encode("utf-8"),
        ContentType="text/csv",
    )
    log.info(f"  → Rechazados: s3://{bucket}/{key} ({len(rejected_df)} filas)")
    return key


# ── PASO 6: Quality Report ────────────────────────────────────────────────────

def write_quality_report(
    s3_client,
    dq_metrics: dict,
    raw_metrics: dict,
    transformed_metrics: dict,
    transform_stats: dict,
    read_metadata: dict,
    bucket: str,
    reports_prefix: str,
    run_timestamp: str,
    job_name: str,
) -> str:
    """Genera y escribe el reporte de calidad en S3."""
    # Tres medidas, una por etapa del pipeline. Con solo dos (raw y curated) no
    # se puede distinguir que arreglo la normalizacion de que se arreglo
    # apartando filas, que es justo la leccion del lab.
    comparison = {}
    for name, curated_rule in dq_metrics["rules"].items():
        raw_rule = raw_metrics.get("rules", {}).get(name, {})
        tr_rule  = transformed_metrics.get("rules", {}).get(name, {})

        if raw_rule.get("passed"):
            resolved_by = None                    # no habia nada que arreglar
        elif tr_rule.get("passed"):
            resolved_by = "transformacion"        # se normalizo el valor
        elif curated_rule["passed"]:
            resolved_by = "rechazo"               # la fila se aparto a rejected/
        else:
            resolved_by = "sin resolver"

        comparison[name] = {
            "raw_score":           raw_rule.get("score"),
            "raw_passed":          raw_rule.get("passed"),
            "raw_failed_rows":     raw_rule.get("failed_rows"),
            "transformed_score":   tr_rule.get("score"),
            "transformed_passed":  tr_rule.get("passed"),
            "curated_score":       curated_rule["score"],
            "curated_passed":      curated_rule["passed"],
            "curated_failed_rows": curated_rule["failed_rows"],
            "resolved_by":         resolved_by,
        }

    report = {
        "job_name":        job_name,
        "run_timestamp":   run_timestamp,
        "source":          read_metadata,
        "transform_stats": transform_stats,
        "data_quality_raw":         raw_metrics,
        "data_quality_transformed": transformed_metrics,
        "data_quality_curated":     dq_metrics,
        "rules_raw_vs_curated": comparison,
        "data_quality":    dq_metrics,
        "summary": {
            "total_rows_input":  dq_metrics["total_rows"],
            "total_rows_valid":  dq_metrics["valid_rows"],
            "total_rows_rejected": dq_metrics["rejected_rows"],
            "overall_dq_score":  f"{dq_metrics['overall_score']:.1%}",
            "rules_passed":      f"{dq_metrics['rules_passed']}/{len(dq_metrics['rules'])}",
            "recommendation": (
                "PASS: Dataset listo para consumo por Analytics y AI"
                if dq_metrics["overall_score"] >= 0.8
                else "REVIEW: Se recomienda revisar los registros rechazados"
            ),
        },
    }

    key = f"{reports_prefix}{run_timestamp}/quality_report.json"
    write_json_to_s3(s3_client, report, bucket, key)

    # También escribir un "latest" para fácil acceso
    latest_key = f"{reports_prefix}latest/quality_report.json"
    write_json_to_s3(s3_client, report, bucket, latest_key)

    return key


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main() -> None:
    args = parse_args()
    run_timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    log.info("=" * 60)
    log.info(f"FinCorp ETL Job — {args.JOB_NAME}")
    log.info(f"Run: {run_timestamp}")
    log.info(f"Bucket: {args.S3_BUCKET}")
    log.info("=" * 60)

    s3_client = boto3.client("s3")

    # ── Paso 1: Leer raw data ────────────────────────────────────────────────
    log.info("\n[PASO 1/5] Leyendo datos raw desde S3...")
    raw_df, read_metadata = read_raw_data(s3_client, args.S3_BUCKET, args.RAW_PREFIX, args.SOURCE_PATH)

    # Medir las reglas ANTES de tocar nada: es la foto del problema real.
    raw_metrics = measure_rules(raw_df)
    log.info(
        f"  Calidad en raw: {raw_metrics['rules_passed']}/{len(raw_metrics['rules'])} "
        f"reglas pasan (score {raw_metrics['overall_score']:.1%})"
    )
    for name, r in raw_metrics["rules"].items():
        if not r["passed"]:
            log.info(f"    FALLA en origen: {name} = {r['score']:.4f} ({r['failed_rows']} filas)")

    # ── Paso 2: Transformar ──────────────────────────────────────────────────
    log.info("\n[PASO 2/5] Aplicando transformaciones...")
    transformed_df, transform_stats = transform_dataframe(raw_df)

    # Segunda medida: ya normalizado, pero sin apartar nada todavia.
    transformed_metrics = measure_rules(transformed_df)

    # ── Paso 3: Data Quality ─────────────────────────────────────────────────
    log.info("\n[PASO 3/5] Evaluando Data Quality...")
    valid_df, rejected_df, dq_metrics = apply_data_quality(transformed_df)

    # ── Paso 4: Escribir curated ─────────────────────────────────────────────
    log.info("\n[PASO 4/5] Escribiendo datos curados (Parquet)...")
    curated_keys = write_curated_data(
        s3_client, valid_df, args.S3_BUCKET, args.CURATED_PREFIX
    )

    # ── Paso 5: Escribir rechazados y quality report ─────────────────────────
    log.info("\n[PASO 5/5] Escribiendo rechazados y quality report...")
    write_rejected_data(
        s3_client, rejected_df, args.S3_BUCKET, args.REJECTED_PREFIX, run_timestamp
    )
    report_key = write_quality_report(
        s3_client, dq_metrics, raw_metrics, transformed_metrics, transform_stats, read_metadata,
        args.S3_BUCKET, args.REPORTS_PREFIX, run_timestamp, args.JOB_NAME
    )

    # ── Resumen final ─────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("RESUMEN DEL JOB")
    log.info("=" * 60)
    log.info(f"  Filas leídas    : {dq_metrics['total_rows']}")
    log.info(f"  Filas válidas   : {dq_metrics['valid_rows']}")
    log.info(f"  Filas rechazadas: {dq_metrics['rejected_rows']}")
    log.info(f"  Score DQ        : {dq_metrics['overall_score']:.1%}")
    log.info(f"  Reglas en raw        : {raw_metrics['rules_passed']}/{len(raw_metrics['rules'])} pasan")
    log.info(f"  Reglas tras transformar: {transformed_metrics['rules_passed']}/{len(transformed_metrics['rules'])} pasan")
    log.info(f"  Reglas en curated    : {dq_metrics['rules_passed']}/{len(dq_metrics['rules'])} pasan")
    log.info(f"  Particiones     : {len(curated_keys)}")
    log.info(f"  Quality report  : s3://{args.S3_BUCKET}/{report_key}")
    log.info("=" * 60)
    log.info("JOB COMPLETADO ✓")

    # Salir con código 0 incluso si hay registros rechazados — los rechazados
    # son esperados en este dataset. Solo fallar si no se pudo escribir nada.
    if not curated_keys and dq_metrics["valid_rows"] > 0:
        log.error("ERROR: No se escribieron particiones curated pero hay registros válidos.")
        sys.exit(1)


if __name__ == "__main__":
    main()
