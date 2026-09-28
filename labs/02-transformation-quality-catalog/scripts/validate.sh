#!/usr/bin/env bash
# =============================================================================
# validate.sh — Lab 2: Verificar outputs del pipeline ETL
# =============================================================================
# Verifica:
#   1. Archivos Parquet en s3://bucket/curated/transactions/
#   2. Tabla transactions en Glue Catalog (financial_platform)
#   3. Quality report en s3://bucket/quality-reports/latest/
#   4. Archivos rechazados en s3://bucket/rejected/
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS ✓]${NC} $*"; }
fail() { echo -e "${RED}[FAIL ✗]${NC} $*"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

FAILURES=0
PROJECT_NAME="${PROJECT_NAME:-fincorp-datalake}"
ENVIRONMENT="${ENVIRONMENT:-lab}"
REGION="${AWS_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo "  Lab 2 — Validación del pipeline ETL"
echo "============================================================"
echo ""

get_lab1_output() { tf_output lab1 "$1"; }
get_lab2_output() { tf_output lab2 "$1"; }

S3_BUCKET=$(get_lab1_output s3_bucket_name)
DB_NAME=$(get_lab2_output glue_database_name)

[ -n "${S3_BUCKET}" ] || { fail "s3_bucket_name no encontrado en el modulo del Lab 1"; exit 1; }
[ -n "${DB_NAME}" ]   || { fail "glue_database_name no encontrado en el modulo del Lab 2"; exit 1; }

# ── [1] Parquet en curated/ ───────────────────────────────────────────────────
echo ""
info "[1/4] Verificando archivos Parquet en curated/transactions/..."

PARQUET_COUNT=$(aws s3 ls "s3://${S3_BUCKET}/curated/transactions/" \
  --recursive \
  --region "${REGION}" 2>/dev/null | grep -c "\.parquet" || echo "0")

if [ "${PARQUET_COUNT}" -gt 0 ]; then
  pass "Archivos Parquet encontrados: ${PARQUET_COUNT}"
  echo ""
  echo "  Estructura de particiones:"
  aws s3 ls "s3://${S3_BUCKET}/curated/transactions/" \
    --recursive \
    --region "${REGION}" 2>/dev/null | \
    grep "\.parquet" | \
    awk '{print "  s3://'${S3_BUCKET}'/" $4}' | head -10
  echo ""
else
  fail "No se encontraron archivos Parquet en s3://${S3_BUCKET}/curated/transactions/"
  echo "       ¿Ejecutaste run_job.sh?"
fi

# ── [2] Tabla en Glue Catalog ────────────────────────────────────────────────
echo ""
info "[2/4] Verificando tabla en Glue Catalog (${DB_NAME})..."

TABLE_EXISTS=$(aws glue get-table \
  --database-name "${DB_NAME}" \
  --name          "transactions" \
  --region        "${REGION}" \
  --query         "Table.Name" \
  --output        text 2>/dev/null || echo "")

if [ "${TABLE_EXISTS}" = "transactions" ]; then
  pass "Tabla ${DB_NAME}.transactions existe en el Catalog"

  # Mostrar schema de la tabla
  echo ""
  echo "  Schema de la tabla:"
  aws glue get-table \
    --database-name "${DB_NAME}" \
    --name          "transactions" \
    --region        "${REGION}" \
    --query         "Table.StorageDescriptor.Columns[*].[Name,Type]" \
    --output        table 2>/dev/null | sed 's/^/  /'
else
  fail "Tabla transactions no encontrada en ${DB_NAME}"
  echo "       ¿Ejecutaste el Crawler? (run_job.sh lo hace automáticamente)"
fi

# ── [3] Quality Report ────────────────────────────────────────────────────────
echo ""
info "[3/4] Verificando quality report..."

REPORT_EXISTS=$(aws s3 ls \
  "s3://${S3_BUCKET}/quality-reports/latest/quality_report.json" \
  --region "${REGION}" 2>/dev/null | wc -l || echo "0")

if [ "${REPORT_EXISTS}" -gt 0 ]; then
  pass "Quality report encontrado"

  REPORT_JSON=$(aws s3 cp \
    "s3://${S3_BUCKET}/quality-reports/latest/quality_report.json" - \
    --region "${REGION}" 2>/dev/null || echo "{}")

  if command -v jq >/dev/null 2>&1 && [ "${REPORT_JSON}" != "{}" ]; then
    SCORE=$(echo "${REPORT_JSON}" | jq -r '.summary.overall_dq_score // "N/A"')
    RULES=$(echo "${REPORT_JSON}" | jq -r '.summary.rules_passed // "N/A"')
    REJECTED=$(echo "${REPORT_JSON}" | jq -r '.data_quality.rejected_rows // "N/A"')
    echo "  Score DQ       : ${SCORE}"
    echo "  Reglas pasadas : ${RULES}"
    echo "  Rechazados     : ${REJECTED} filas"
  fi
else
  fail "Quality report no encontrado en s3://${S3_BUCKET}/quality-reports/latest/"
fi

# ── [4] Archivos rechazados ───────────────────────────────────────────────────
echo ""
info "[4/4] Verificando archivos rechazados..."

REJECTED_COUNT=$(aws s3 ls "s3://${S3_BUCKET}/rejected/" \
  --recursive \
  --region "${REGION}" 2>/dev/null | grep -c "rejected.csv" || echo "0")

if [ "${REJECTED_COUNT}" -gt 0 ]; then
  pass "Archivos rechazados encontrados: ${REJECTED_COUNT}"
  echo "  (Esto es ESPERADO — el dataset tiene problemas deliberados)"

  # Contar filas rechazadas
  REJECTED_KEY=$(aws s3 ls "s3://${S3_BUCKET}/rejected/" \
    --recursive \
    --region "${REGION}" 2>/dev/null | grep "rejected.csv" | tail -1 | awk '{print $4}')
  if [ -n "${REJECTED_KEY}" ]; then
    REJECTED_ROWS=$(aws s3 cp "s3://${S3_BUCKET}/${REJECTED_KEY}" - \
      --region "${REGION}" 2>/dev/null | wc -l || echo "1")
    REJECTED_DATA=$((REJECTED_ROWS - 1))
    echo "  Filas rechazadas en el último run: ~${REJECTED_DATA}"
  fi
else
  pass "Sin archivos rechazados (dataset limpio o job no ejecutado)"
fi

# ── Resumen ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
if [ "${FAILURES}" -eq 0 ]; then
  echo -e "${GREEN}  ✅ Validación: PASS (0 fallos)${NC}"
  echo ""
  echo "  El pipeline ETL completó correctamente:"
  echo "  ✓ Datos curados en S3 (Parquet particionado)"
  echo "  ✓ Tabla registrada en Glue Data Catalog"
  echo "  ✓ Quality report disponible"
  echo "  ✓ Registros rechazados en zona rejected/"
  echo ""
  echo "  Próximo paso: Lab 3 → RAG sobre la plataforma de datos"
else
  echo -e "${RED}  ❌ Validación: FAIL (${FAILURES} fallos)${NC}"
  echo ""
  echo "  Solución más común: ejecuta ./run_job.sh primero"
fi
echo "============================================================"
echo ""

exit ${FAILURES}
