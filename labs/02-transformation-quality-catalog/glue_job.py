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

# ── Constantes ────────────────────────────────────────────────────────────────
VALID_TRANSACTION_TYPES = {"DEPOSIT", "WITHDRAWAL", "TRANSFER"}
VALID_STATUSES           = {"PENDING", "COMPLETED", "FAILED", "PROCESSING"}
VALID_CURRENCIES         = {"USD", "PEN", "EUR"}

# Mapa de normalización para transaction_type (español → inglés)
TYPE_NORMALIZATION = {
    "DEPÓSITO":      "DEPOSIT",
    "DEPOSITO":      "DEPOSIT",
    "RETIRO":        "WITHDRAWAL",
    "TRANSFERENCIA": "TRANSFER",
}

# ── Argumentos del job ────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    """
    En Glue Python Shell los argumentos se pasan con prefijo '--'.
    En desarrollo local también funciona igual.
    """
    parser = argparse.ArgumentParser(description="FinCorp Glue ETL Job — Lab 2")
    parser.add_argument("--S3_BUCKET",       required=True,              help="Nombre del bucket S3")
    parser.add_argument("--RAW_PREFIX",      default="raw/",             help="Prefijo de la zona raw")
    parser.add_argument("--CURATED_PREFIX",  default="curated/",         help="Prefijo de la zona curated")
    parser.add_argument("--REJECTED_PREFIX", default="rejected/",        help="Prefijo para registros rechazados")
    parser.add_argument("--REPORTS_PREFIX",  default="quality-reports/", help="Prefijo para reportes de calidad")
    parser.add_argument("--DATABASE_NAME",   default="financial_platform",help="Nombre de la DB en Glue Catalog")
    parser.add_argument("--JOB_NAME",        default="fincorp-glue-etl",  help="Nombre del Glue job (para logs)")
    return parser.parse_args()


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

def read_raw_data(s3_client, bucket: str, raw_prefix: str) -> tuple[pd.DataFrame, dict]:
    """Lee todos los CSV de transactions desde S3 raw/ y los concatena."""
    prefix = f"{raw_prefix}transactions/"
    keys = list_s3_files(s3_client, bucket, prefix)

    if not keys:
        # Fallback: buscar en staging/ (para desarrollo sin DMS)
        prefix_staging = "staging/transactions/"
        keys = list_s3_files(s3_client, bucket, prefix_staging)
        log.warning(f"No se encontraron CSVs en {raw_prefix}transactions/. Usando staging/.")

    if not keys:
        raise FileNotFoundError(
            f"No se encontraron archivos CSV en s3://{bucket}/{raw_prefix}transactions/ "
            f"ni en staging/. Asegúrate de que el Lab 1 completó correctamente."
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
      b. Normalizar transaction_type (español → inglés)
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

    # b. Normalizar transaction_type (upper + mapa español→inglés)
    original_types = df["transaction_type"].copy()
    df["transaction_type"] = df["transaction_type"].str.strip().str.upper()
    df["transaction_type"] = df["transaction_type"].replace(TYPE_NORMALIZATION)
    stats["types_normalized"] = int(
        (original_types.str.upper().isin(set(TYPE_NORMALIZATION.keys()))).sum()
    )
    log.info(f"  Tipos normalizados (español→inglés): {stats['types_normalized']}")

    # c. Normalizar status (upper case)
    original_status = df["status"].copy()
    df["status"] = df["status"].str.strip().str.upper()
    stats["status_normalized"] = int(
        (original_status != df["status"]).sum()
    )
    log.info(f"  Status normalizados (case): {stats['status_normalized']}")

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

def apply_data_quality(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """
    Evalúa las reglas de calidad y separa registros válidos de rechazados.

    Reglas aplicadas (equivalentes al ruleset DQDL en quality_rules.dqdl):
      R1: amount IS NOT NULL (Completeness >= 0.95 → rechazar individuales con NULL)
      R2: transaction_id IS NOT NULL (IsComplete)
      R3: customer_id IS NOT NULL (IsComplete)
      R4: transaction_type IN VALID_TRANSACTION_TYPES (ColumnValues)
      R5: status IN VALID_STATUSES (ColumnValues)
      R6: currency IN VALID_CURRENCIES (ColumnValues)
      R7: NO transaction_id duplicado (Uniqueness)

    Un registro es rechazado si falla cualquier regla individual.
    Las métricas de completeness/uniqueness se calculan sobre el dataset completo.
    """
    total = len(df)

    # Crear máscara de rechazo por cada regla
    mask_null_amount    = df["amount"].isna()
    mask_null_txn_id    = df["transaction_id"].isna()
    mask_null_cust_id   = df["customer_id"].isna()
    mask_invalid_type   = ~df["transaction_type"].isin(VALID_TRANSACTION_TYPES)
    mask_invalid_status = ~df["status"].isin(VALID_STATUSES)
    mask_invalid_curr   = ~df["currency"].isin(VALID_CURRENCIES)

    # Duplicados: marcar TODAS las ocurrencias del ID duplicado como rechazadas
    dup_mask            = df.duplicated(subset=["transaction_id"], keep=False)

    # Máscara combinada: rechazar si falla ALGUNA regla
    reject_mask = (
        mask_null_amount    |
        mask_null_txn_id    |
        mask_null_cust_id   |
        mask_invalid_type   |
        mask_invalid_status |
        mask_invalid_curr   |
        dup_mask
    )

    valid_df    = df[~reject_mask].copy()
    rejected_df = df[reject_mask].copy()

    # Agregar columna con razón(es) de rechazo
    reasons = pd.Series([""] * total, index=df.index)
    reasons[mask_null_amount]    += "NULL_AMOUNT;"
    reasons[mask_null_txn_id]    += "NULL_TXN_ID;"
    reasons[mask_null_cust_id]   += "NULL_CUST_ID;"
    reasons[mask_invalid_type]   += "INVALID_TYPE;"
    reasons[mask_invalid_status] += "INVALID_STATUS;"
    reasons[mask_invalid_curr]   += "INVALID_CURRENCY;"
    reasons[dup_mask]             += "DUPLICATE_TXN_ID;"
    rejected_df["rejection_reason"] = reasons[reject_mask].values

    # Métricas para el quality report
    completeness_amount    = float((~mask_null_amount).sum()) / total if total > 0 else 1.0
    completeness_txn_id    = float((~mask_null_txn_id).sum()) / total if total > 0 else 1.0
    uniqueness_txn_id      = float(df["transaction_id"].nunique()) / total if total > 0 else 1.0
    validity_type          = float((~mask_invalid_type).sum()) / total if total > 0 else 1.0
    validity_status        = float((~mask_invalid_status).sum()) / total if total > 0 else 1.0
    validity_currency      = float((~mask_invalid_curr).sum()) / total if total > 0 else 1.0

    dq_metrics = {
        "total_rows":              total,
        "valid_rows":              len(valid_df),
        "rejected_rows":           len(rejected_df),
        "acceptance_rate":         round(len(valid_df) / total, 4) if total > 0 else 1.0,
        "rules": {
            "completeness_amount": {
                "score":    round(completeness_amount, 4),
                "threshold": 0.95,
                "passed":   completeness_amount >= 0.95,
                "failed_rows": int(mask_null_amount.sum()),
            },
            "completeness_transaction_id": {
                "score":    round(completeness_txn_id, 4),
                "threshold": 1.0,
                "passed":   completeness_txn_id >= 1.0,
                "failed_rows": int(mask_null_txn_id.sum()),
            },
            "uniqueness_transaction_id": {
                "score":    round(uniqueness_txn_id, 4),
                "threshold": 1.0,
                "passed":   uniqueness_txn_id >= 1.0,
                "failed_rows": int(dup_mask.sum()),
            },
            "validity_transaction_type": {
                "score":    round(validity_type, 4),
                "threshold": 1.0,
                "passed":   validity_type >= 1.0,
                "failed_rows": int(mask_invalid_type.sum()),
                "invalid_values": df[mask_invalid_type]["transaction_type"].value_counts().to_dict(),
            },
            "validity_status": {
                "score":    round(validity_status, 4),
                "threshold": 1.0,
                "passed":   validity_status >= 1.0,
                "failed_rows": int(mask_invalid_status.sum()),
            },
            "validity_currency": {
                "score":    round(validity_currency, 4),
                "threshold": 1.0,
                "passed":   validity_currency >= 1.0,
                "failed_rows": int(mask_invalid_curr.sum()),
            },
        },
    }

    # rules_passed/failed/overall dependen de dq_metrics["rules"], por eso se
    # calculan aqui y no dentro del literal de arriba.
    dq_metrics["rules_passed"] = sum(
        1 for r in dq_metrics["rules"].values() if r["passed"]
    )
    dq_metrics["rules_failed"] = len(dq_metrics["rules"]) - dq_metrics["rules_passed"]
    dq_metrics["overall_score"] = round(
        dq_metrics["rules_passed"] / len(dq_metrics["rules"]), 4
    )

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
    transform_stats: dict,
    read_metadata: dict,
    bucket: str,
    reports_prefix: str,
    run_timestamp: str,
    job_name: str,
) -> str:
    """Genera y escribe el reporte de calidad en S3."""
    report = {
        "job_name":        job_name,
        "run_timestamp":   run_timestamp,
        "source":          read_metadata,
        "transform_stats": transform_stats,
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
    raw_df, read_metadata = read_raw_data(s3_client, args.S3_BUCKET, args.RAW_PREFIX)

    # ── Paso 2: Transformar ──────────────────────────────────────────────────
    log.info("\n[PASO 2/5] Aplicando transformaciones...")
    transformed_df, transform_stats = transform_dataframe(raw_df)

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
        s3_client, dq_metrics, transform_stats, read_metadata,
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
    log.info(f"  Reglas pasadas  : {dq_metrics['rules_passed']}/{len(dq_metrics['rules'])}")
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
