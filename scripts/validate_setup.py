#!/usr/bin/env python3
"""
validate_setup.py — Verificación pre-demo de los 3 labs.

Se ejecuta ANTES de la charla para confirmar que todo lo que la demo necesita
está en pie. No crea ni modifica nada: solo lee estado.

Uso:
    python scripts/validate_setup.py              # los 3 labs
    python scripts/validate_setup.py --lab 1      # solo Lab 1
    python scripts/validate_setup.py --lab 3 --lab 2

Variables de entorno (mismos defaults que los scripts .sh de cada lab):
    PROJECT_NAME  (default: fincorp-datalake)
    ENVIRONMENT   (default: lab)
    AWS_REGION    (default: us-east-1)

Código de salida:
    0  todo OK (los WARN no bloquean)
    1  al menos un check crítico falló
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
import urllib.error
import urllib.request

try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
except ImportError:
    sys.exit("Falta boto3. Instálalo con: pip install boto3")

# ── Configuración ─────────────────────────────────────────────────────────────
PROJECT_NAME = os.getenv("PROJECT_NAME", "fincorp-datalake")
ENVIRONMENT = os.getenv("ENVIRONMENT", "lab")
REGION = os.getenv("AWS_REGION", "us-east-1")

REPO_ROOT = Path(__file__).resolve().parent.parent
INFRA_DIR = REPO_ROOT / "infra"

GLUE_DATABASE = "financial_platform"
GLUE_TABLE = "transactions"
DYNAMO_TABLE = os.getenv("DYNAMODB_TABLE", "rag_user_memory")

EMBEDDING_MODEL = os.getenv("BEDROCK_EMBEDDING_MODEL", "amazon.titan-embed-text-v2:0")
LLM_MODEL = os.getenv("BEDROCK_LLM_MODEL", "amazon.nova-pro-v1:0")

# ── Salida ────────────────────────────────────────────────────────────────────
GREEN, RED, YELLOW, BLUE, DIM, NC = (
    "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0;34m", "\033[2m", "\033[0m"
)

failures: list[str] = []
warnings: list[str] = []


def ok(msg: str, detail: str = "") -> None:
    print(f"  {GREEN}✓{NC} {msg}" + (f" {DIM}— {detail}{NC}" if detail else ""))


def bad(msg: str, detail: str = "") -> None:
    failures.append(msg)
    print(f"  {RED}✗{NC} {msg}" + (f" {DIM}— {detail}{NC}" if detail else ""))


def warn(msg: str, detail: str = "") -> None:
    warnings.append(msg)
    print(f"  {YELLOW}⚠{NC} {msg}" + (f" {DIM}— {detail}{NC}" if detail else ""))


def header(title: str) -> None:
    print(f"\n{BLUE}{'─' * 62}{NC}\n{BLUE}  {title}{NC}\n{BLUE}{'─' * 62}{NC}")


# ── Helpers ───────────────────────────────────────────────────────────────────
def tf_outputs(module: str) -> dict[str, str] | None:
    """
    Outputs de un modulo Terraform, o None si no se pudo leer.

    Lee `terraform output -json` en vez de los outputs de un stack
    CloudFormation: la infraestructura se migro a Terraform.
    """
    module_dir = INFRA_DIR / module
    if not module_dir.is_dir():
        return None
    try:
        proc = subprocess.run(
            ["terraform", f"-chdir={module_dir}", "output", "-json"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    try:
        raw = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    # terraform output -json da {"nombre": {"value": ..., "sensitive": ...}}
    return {k: str(v.get("value", "")) for k, v in raw.items()}


def check_module(module: str, label: str) -> dict[str, str] | None:
    outputs = tf_outputs(module)
    if outputs is None:
        bad(
            f"No se pudieron leer los outputs de {label}",
            f"infra/{module} — ¿terraform init hecho? ¿terraform en el PATH?",
        )
        return None
    if not outputs:
        bad(f"{label} sin desplegar", f"infra/{module} no tiene outputs")
        return None
    ok(f"Modulo {label}", f"infra/{module} · {len(outputs)} outputs")
    return outputs


def count_s3(s3, bucket: str, prefix: str) -> int:
    paginator = s3.get_paginator("list_objects_v2")
    total = 0
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        total += len([o for o in page.get("Contents", []) if not o["Key"].endswith("/")])
    return total


# ── Checks: prerrequisitos ────────────────────────────────────────────────────
def check_prerequisites(session) -> bool:
    header("Prerrequisitos")
    try:
        ident = session.client("sts").get_caller_identity()
    except (ClientError, BotoCoreError) as exc:
        bad("Credenciales AWS", str(exc)[:80])
        return False
    ok("Credenciales AWS", f"cuenta {ident['Account']}")

    if session.region_name != REGION:
        warn("Región", f"sesión en {session.region_name}, se esperaba {REGION}")
    else:
        ok("Región", REGION)

    # Bedrock: los modelos deben estar habilitados en la cuenta
    try:
        models = session.client("bedrock", region_name=REGION).list_foundation_models()
        available = {m["modelId"] for m in models["modelSummaries"]}
    except (ClientError, BotoCoreError) as exc:
        bad("Bedrock accesible", str(exc)[:80])
        return True

    for model in (EMBEDDING_MODEL, LLM_MODEL):
        base = model.split(":")[0]
        if any(m.split(":")[0] == base for m in available):
            ok("Modelo Bedrock disponible", model)
        else:
            bad("Modelo Bedrock NO disponible", f"{model} — habilítalo en la consola")
    return True


# ── Checks: Lab 1 ─────────────────────────────────────────────────────────────
def check_lab1(session) -> None:
    header("Lab 1 — Ingestion + Data Lake")
    outputs = check_module("lab1", "Lab 1")
    if not outputs:
        return

    # RDS MySQL
    rds = session.client("rds")
    db_id = f"{PROJECT_NAME}-{ENVIRONMENT}-mysql"
    try:
        db = rds.describe_db_instances(DBInstanceIdentifier=db_id)["DBInstances"][0]
        if db["DBInstanceStatus"] == "available":
            ok("RDS MySQL", f"{db_id} · {db['EngineVersion']}")
        else:
            bad("RDS MySQL no disponible", db["DBInstanceStatus"])
        # CDC necesita backups automáticos activos para retener el binlog
        if db.get("BackupRetentionPeriod", 0) < 1:
            bad("RDS backup retention = 0", "DMS CDC requiere binlog retenido (>= 1 día)")
    except ClientError as exc:
        bad("RDS MySQL", str(exc)[:80])

    # Tarea DMS
    task_arn = outputs.get("dms_task_arn")
    if not task_arn:
        bad("Output dms_task_arn ausente del modulo")
    else:
        try:
            task = session.client("dms").describe_replication_tasks(
                Filters=[{"Name": "replication-task-arn", "Values": [task_arn]}]
            )["ReplicationTasks"][0]
            status = task["Status"]
            if status in ("running", "stopped"):
                pct = task.get("ReplicationTaskStats", {}).get("FullLoadProgressPercent", 0)
                ok("Tarea DMS", f"{status} · full load {pct}%")
            elif status == "ready":
                warn("Tarea DMS aún no iniciada", "ejecuta start-replication-task")
            else:
                bad("Tarea DMS en estado inesperado", status)
        except (ClientError, IndexError) as exc:
            bad("Tarea DMS", str(exc)[:80])

    # Zona raw del data lake
    bucket = outputs.get("s3_bucket_name")
    if not bucket:
        bad("Output s3_bucket_name ausente del modulo")
        return
    s3 = session.client("s3")
    for table in ("transactions", "customers"):
        try:
            n = count_s3(s3, bucket, f"raw/{table}/")
        except ClientError as exc:
            bad(f"S3 raw/{table}/", str(exc)[:80])
            continue
        if n:
            ok(f"S3 raw/{table}/", f"{n} objeto(s)")
        else:
            bad(f"S3 raw/{table}/ vacío", "la tarea DMS no ha replicado aún")


# ── Checks: Lab 2 ─────────────────────────────────────────────────────────────
def check_lab2(session) -> None:
    header("Lab 2 — Transformation + Quality + Catalog")
    outputs = check_module("lab2", "Lab 2")
    if not outputs:
        return

    glue = session.client("glue")
    job = outputs.get("glue_job_name")
    if job:
        try:
            glue.get_job(JobName=job)
            ok("Glue Job registrado", job)
        except ClientError:
            bad("Glue Job no encontrado", job)

    # Tabla en el catálogo: la crea el crawler tras el primer run del job
    try:
        table = glue.get_table(DatabaseName=GLUE_DATABASE, Name=GLUE_TABLE)["Table"]
        cols = len(table["StorageDescriptor"]["Columns"])
        ok("Glue Catalog", f"{GLUE_DATABASE}.{GLUE_TABLE} · {cols} columnas")
    except ClientError:
        warn(
            f"Tabla {GLUE_DATABASE}.{GLUE_TABLE} aún no existe",
            "se crea al correr run_job.sh (crawler)",
        )

    bucket = outputs.get("s3_bucket_name")
    if not bucket:
        warn("No se pudo leer el bucket", "se omite la verificación de curated/")
        return

    s3 = session.client("s3")
    try:
        n = count_s3(s3, bucket, "curated/transactions/")
    except ClientError as exc:
        bad("S3 curated/", str(exc)[:80])
        return
    if n:
        ok("S3 curated/transactions/", f"{n} archivo(s) Parquet")
    else:
        warn("S3 curated/ vacío", "el Glue Job aún no ha corrido — es lo esperado pre-demo")

    try:
        s3.head_object(Bucket=bucket, Key="quality-reports/latest/quality_report.json")
        ok("Quality report presente", "quality-reports/latest/")
    except ClientError:
        warn("Quality report ausente", "se genera al correr run_job.sh")


# ── Checks: Lab 3 ─────────────────────────────────────────────────────────────
def check_lab3(session) -> None:
    header("Lab 3 — Data to AI (RAG)")
    outputs = check_module("lab3", "Lab 3")
    if not outputs:
        return

    # Aurora PostgreSQL + pgvector. Aurora vive en subnets privadas: desde un
    # laptop no es alcanzable, así que verificamos estado del cluster, no conexión.
    try:
        clusters = session.client("rds").describe_db_clusters()["DBClusters"]
        aurora = [c for c in clusters if c["Engine"] == "aurora-postgresql"]
        if aurora and aurora[0]["Status"] == "available":
            ok("Aurora PostgreSQL", f"v{aurora[0]['EngineVersion']} · available")
        elif aurora:
            bad("Aurora no disponible", aurora[0]["Status"])
        else:
            bad("No se encontró cluster Aurora PostgreSQL")
    except ClientError as exc:
        bad("Aurora", str(exc)[:80])

    # ElastiCache Redis
    try:
        groups = session.client("elasticache").describe_replication_groups()[
            "ReplicationGroups"
        ]
        if groups and groups[0]["Status"] == "available":
            ok("ElastiCache Redis", groups[0]["ReplicationGroupId"])
        elif groups:
            bad("Redis no disponible", groups[0]["Status"])
        else:
            warn("No se encontró replication group de Redis")
    except ClientError as exc:
        bad("Redis", str(exc)[:80])

    # DynamoDB — memoria de largo plazo
    try:
        table = session.client("dynamodb").describe_table(TableName=DYNAMO_TABLE)["Table"]
        if table["TableStatus"] == "ACTIVE":
            ok("DynamoDB", f"{DYNAMO_TABLE} · {table['ItemCount']} item(s)")
        else:
            bad("DynamoDB no activa", table["TableStatus"])
    except ClientError:
        bad("Tabla DynamoDB no existe", DYNAMO_TABLE)

    # EC2 + Streamlit
    instance_id = outputs.get("app_server_instance_id")
    if instance_id:
        try:
            res = session.client("ec2").describe_instances(InstanceIds=[instance_id])
            state = res["Reservations"][0]["Instances"][0]["State"]["Name"]
            if state == "running":
                ok("EC2 App Server", instance_id)
            else:
                bad("EC2 App Server no está corriendo", state)
        except (ClientError, IndexError) as exc:
            bad("EC2 App Server", str(exc)[:80])

    url = outputs.get("streamlit_url")
    if not url:
        warn("Output streamlit_url ausente del modulo")
        return
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:  # noqa: S310
            if resp.status == 200:
                ok("Streamlit responde", url)
            else:
                bad("Streamlit devolvió HTTP inesperado", f"{resp.status} · {url}")
    except (urllib.error.URLError, OSError) as exc:
        bad("Streamlit no responde", f"{url} — {str(exc)[:60]}")


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Verificación pre-demo de los 3 labs.")
    parser.add_argument(
        "--lab",
        action="append",
        choices=["1", "2", "3"],
        help="Lab a verificar (repetible). Por defecto: los tres.",
    )
    args = parser.parse_args()
    labs = set(args.lab) if args.lab else {"1", "2", "3"}

    print(f"\n{BLUE}{'=' * 62}{NC}")
    print(f"{BLUE}  FinCorp Data Platform — validación pre-demo{NC}")
    print(f"{BLUE}{'=' * 62}{NC}")
    print(f"  Proyecto: {PROJECT_NAME}-{ENVIRONMENT}   Región: {REGION}")

    session = boto3.Session(region_name=REGION)
    if not check_prerequisites(session):
        print(f"\n{RED}Sin credenciales AWS utilizables. Aborta.{NC}\n")
        return 1

    if "1" in labs:
        check_lab1(session)
    if "2" in labs:
        check_lab2(session)
    if "3" in labs:
        check_lab3(session)

    header("Resumen")
    if failures:
        print(f"  {RED}{len(failures)} check(s) fallaron:{NC}")
        for f in failures:
            print(f"    {RED}·{NC} {f}")
    if warnings:
        print(f"  {YELLOW}{len(warnings)} advertencia(s):{NC}")
        for w in warnings:
            print(f"    {YELLOW}·{NC} {w}")
    if not failures and not warnings:
        print(f"  {GREEN}Todo listo para el demo.{NC}")
    elif not failures:
        print(f"  {GREEN}Sin fallos críticos. Revisa las advertencias.{NC}")
    print()
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
