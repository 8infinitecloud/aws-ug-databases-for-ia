#!/usr/bin/env python3
"""
run_pipeline_local.py — ejecuta el pipeline COMPLETO del Lab 2 sin AWS.

Los tests (tests/test_glue_job.py) prueban las funciones puras del Glue job.
Esto ejecuta el job **entero**, el mismo `main()` que corre en AWS, contra un
directorio local que hace de bucket. Sirve para:

  - Ver el quality report de verdad, con las 8 reglas en las tres etapas
  - Inspeccionar el Parquet particionado y la zona rejected/ con sus motivos
  - Iterar sobre las reglas de calidad sin esperar 20 segundos de Glue ni pagar
  - Entender el Lab 2 antes de tener una cuenta AWS

No es una reimplementacion: se sustituye el cliente de S3 por un doble que
guarda en disco. El job no sabe la diferencia, porque solo usa tres metodos
(get_object, get_paginator, put_object).

Uso:
    python scripts/run_pipeline_local.py
    python scripts/run_pipeline_local.py --keep      # no borrar la salida al terminar
    python scripts/run_pipeline_local.py --out /tmp/mi-lake

Requiere: pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GLUE_JOB = REPO_ROOT / "labs/02-transformation-quality-catalog/glue_job.py"
DATASET = REPO_ROOT / "labs/01-ingestion-data-lake/dataset"

GREEN, RED, YELLOW, BLUE, DIM, NC = (
    "\033[0;32m", "\033[0;31m", "\033[1;33m", "\033[0;34m", "\033[2m", "\033[0m"
)


# ── El doble de S3: un directorio local ──────────────────────────────────────
class LocalS3:
    """
    Implementa la parte de la API de S3 que usa glue_job.py, sobre el disco.

    Las claves se traducen a rutas relativas dentro de `root`, asi que la
    estructura de directorios que queda es identica a la del bucket real.
    """

    def __init__(self, root: Path):
        self.root = root
        self.writes: list[tuple[str, int]] = []

    def _path(self, key: str) -> Path:
        return self.root / key

    def get_object(self, Bucket: str, Key: str):  # noqa: N803 (firma de boto3)
        path = self._path(Key)
        if not path.is_file():
            # glue_job captura ClientError; aqui basta con que falle claro.
            raise FileNotFoundError(f"No existe {path}")
        return {"Body": io.BytesIO(path.read_bytes())}

    def put_object(self, Bucket: str, Key: str, Body, **kwargs):  # noqa: N803
        path = self._path(Key)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = Body if isinstance(Body, bytes) else str(Body).encode("utf-8")
        path.write_bytes(data)
        self.writes.append((Key, len(data)))
        return {}

    def get_paginator(self, operation: str):
        if operation != "list_objects_v2":
            raise NotImplementedError(operation)
        return _LocalPaginator(self.root)


class _LocalPaginator:
    def __init__(self, root: Path):
        self.root = root

    def paginate(self, Bucket: str, Prefix: str = "", **kwargs):  # noqa: N803
        base = self.root
        contents = [
            {"Key": str(p.relative_to(base)), "Size": p.stat().st_size}
            for p in sorted(base.rglob("*"))
            if p.is_file() and str(p.relative_to(base)).startswith(Prefix)
        ]
        return [{"Contents": contents}] if contents else [{}]


# ── Utilidades ───────────────────────────────────────────────────────────────
def load_glue_job():
    spec = importlib.util.spec_from_file_location("glue_job", GLUE_JOB)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["glue_job"] = mod
    spec.loader.exec_module(mod)
    return mod


def seed_raw_zone(root: Path) -> None:
    """
    Copia el dataset a raw/, imitando lo que deja DMS.

    DMS nombra los ficheros LOAD00000001.csv, y el job los busca por extension,
    asi que el nombre exacto da igual. Se usa el de DMS para que la estructura
    local se parezca a la real.
    """
    for tabla in ("transactions", "customers"):
        destino = root / "raw" / tabla / "LOAD00000001.csv"
        destino.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(DATASET / f"{tabla}.csv", destino)


def tree(root: Path, prefix: str = "") -> list[str]:
    lineas = []
    for path in sorted(root.rglob("*")):
        if path.is_file():
            rel = path.relative_to(root)
            size = path.stat().st_size
            lineas.append(f"  {prefix}{rel}  {DIM}({size:,} bytes){NC}")
    return lineas


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--out", type=Path, help="Directorio que hace de bucket")
    parser.add_argument("--keep", action="store_true", help="No borrar la salida al terminar")
    args = parser.parse_args()

    root = args.out or (REPO_ROOT / ".local-lake")
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)

    print(f"\n{BLUE}{'=' * 64}{NC}")
    print(f"{BLUE}  Lab 2 en local — el pipeline completo, sin AWS{NC}")
    print(f"{BLUE}{'=' * 64}{NC}")
    print(f"  Bucket simulado: {root}\n")

    print(f"{BLUE}[1/3]{NC} Sembrando la zona raw/ con el dataset del Lab 1...")
    seed_raw_zone(root)
    print(f"  {GREEN}✓{NC} raw/transactions/ y raw/customers/ listos "
          f"{DIM}(como los dejaria DMS){NC}\n")

    print(f"{BLUE}[2/3]{NC} Ejecutando glue_job.main()...")
    print(f"{DIM}{'─' * 64}{NC}")

    gj = load_glue_job()
    fake_s3 = LocalS3(root)

    # El job hace boto3.client("s3") dentro de main(): se sustituye ahi.
    gj.boto3.client = lambda *a, **k: fake_s3

    argv_original = sys.argv
    sys.argv = [
        "glue_job.py",
        "--S3_BUCKET", "bucket-local",
        "--JOB_NAME", "fincorp-local-etl",
    ]
    try:
        gj.main()
    except SystemExit as exc:
        if exc.code:
            print(f"\n{RED}[✗]{NC} El job salio con codigo {exc.code}")
            return exc.code
    finally:
        sys.argv = argv_original

    print(f"{DIM}{'─' * 64}{NC}")
    print(f"  {GREEN}✓{NC} Job completado. {len(fake_s3.writes)} objetos escritos.\n")

    print(f"{BLUE}[3/3]{NC} Lo que produjo el pipeline:\n")
    for linea in tree(root):
        print(linea)

    # El quality report es el artefacto que merece la pena mirar.
    report_path = root / "quality-reports/latest/quality_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text())
        print(f"\n{BLUE}  Calidad por etapa{NC}")
        print(f"  {'regla':32s} {'raw':>6s} {'transf':>8s} {'curated':>8s}   resuelto por")
        print(f"  {'-' * 74}")
        for nombre, datos in report.get("rules_raw_vs_curated", {}).items():
            def marca(valor):
                if valor is None:
                    return "  ?"
                return f"{GREEN}PASS{NC}" if valor else f"{RED}FAIL{NC}"
            resuelto = datos.get("resolved_by") or "-"
            print(
                f"  {nombre:32s} {marca(datos.get('raw_passed')):>15s} "
                f"{marca(datos.get('transformed_passed')):>17s} "
                f"{marca(datos.get('curated_passed')):>17s}   {resuelto}"
            )

        resumen = report.get("summary", {})
        print(f"\n  {DIM}filas: {resumen.get('total_rows_input')} leidas → "
              f"{resumen.get('total_rows_valid')} validas / "
              f"{resumen.get('total_rows_rejected')} rechazadas{NC}")

    # Un motivo de rechazo real, para que se vea que no se borra nada.
    rejected = sorted((root / "rejected").rglob("*.csv")) if (root / "rejected").is_dir() else []
    if rejected:
        primeras = rejected[0].read_text(encoding="utf-8").splitlines()[:3]
        print(f"\n{BLUE}  Muestra de rejected/{NC} {DIM}(nada se borra: se aparta con su motivo){NC}")
        for linea in primeras:
            print(f"  {DIM}{linea[:110]}{NC}")

    print(f"\n{BLUE}{'=' * 64}{NC}")
    if args.keep:
        print(f"  Salida en {root}")
        print(f"  {DIM}Inspecciona el Parquet:{NC}")
        print(f"    python -c \"import pandas as pd; import glob; "
              f"print(pd.read_parquet(glob.glob('{root}/curated/**/*.parquet', recursive=True)[0]).head())\"")
    else:
        shutil.rmtree(root)
        print(f"  {DIM}Salida borrada. Usa --keep para conservarla.{NC}")
    print(f"{BLUE}{'=' * 64}{NC}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
