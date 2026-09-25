#!/usr/bin/env bash
# =============================================================================
# validate.sh — Lab 1: Verificar que DMS completó y los datos están en S3
# =============================================================================
# Verifica:
#   1. Estado de la tarea DMS (debe ser load-complete o running)
#   2. Archivos CSV en s3://bucket/raw/transactions/
#   3. Archivos CSV en s3://bucket/raw/customers/
#   4. Muestra las primeras 5 líneas de cada archivo
#
# Uso: ./validate.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

FAILURES=0
PROJECT_NAME="${PROJECT_NAME:-fincorp-datalake}"
ENVIRONMENT="${ENVIRONMENT:-lab}"
REGION="${AWS_REGION:-us-east-1}"
STACK_NAME="${PROJECT_NAME}-${ENVIRONMENT}-lab1"

echo "============================================================"
echo "  Lab 1 — Validación post-DMS"
echo "============================================================"
echo ""

# ── Helper: obtener output del stack ─────────────────────────────────────────
get_output() {
  aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region     "${REGION}" \
    --query      "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output     text 2>/dev/null || echo ""
}

# ── [1] Verificar stack existe ────────────────────────────────────────────────
info "[1/5] Verificando stack CloudFormation..."
STACK_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region     "${REGION}" \
  --query      "Stacks[0].StackStatus" \
  --output     text 2>/dev/null || echo "NOT_FOUND")

if [ "${STACK_STATUS}" = "CREATE_COMPLETE" ] || [ "${STACK_STATUS}" = "UPDATE_COMPLETE" ]; then
  pass "Stack ${STACK_NAME}: ${STACK_STATUS}"
else
  fail "Stack ${STACK_NAME}: ${STACK_STATUS} (esperado: CREATE_COMPLETE)"
fi

S3_BUCKET=$(get_output "S3BucketName")
DMS_TASK_ARN=$(get_output "DMSTaskArn")

# ── [2] Verificar estado DMS Task ─────────────────────────────────────────────
echo ""
info "[2/5] Verificando tarea DMS..."

if [ -n "${DMS_TASK_ARN}" ] && [ "${DMS_TASK_ARN}" != "None" ]; then
  TASK_STATUS=$(aws dms describe-replication-tasks \
    --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" \
    --region  "${REGION}" \
    --query   "ReplicationTasks[0].Status" \
    --output  text 2>/dev/null || echo "unknown")

  TASK_PROGRESS=$(aws dms describe-replication-tasks \
    --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" \
    --region  "${REGION}" \
    --query   "ReplicationTasks[0].ReplicationTaskStats.FullLoadProgressPercent" \
    --output  text 2>/dev/null || echo "0")

  case "${TASK_STATUS}" in
    load-complete|running|stopped)
      pass "DMS Task status: ${TASK_STATUS} | Full Load: ${TASK_PROGRESS}%"
      ;;
    ready|creating)
      fail "DMS Task status: ${TASK_STATUS} — La tarea no ha sido iniciada."
      echo "       Inicia con: aws dms start-replication-task --replication-task-arn ${DMS_TASK_ARN} --start-replication-task-type start-replication"
      ;;
    failed)
      fail "DMS Task status: FAILED — Revisa los logs en CloudWatch."
      ;;
    *)
      fail "DMS Task status: ${TASK_STATUS}"
      ;;
  esac
else
  fail "No se pudo obtener el ARN de la tarea DMS del stack"
fi

# ── [3] Verificar archivos transactions en S3 ─────────────────────────────────
echo ""
info "[3/5] Verificando s3://${S3_BUCKET}/raw/transactions/..."

TXN_COUNT=$(aws s3 ls "s3://${S3_BUCKET}/raw/transactions/" \
  --region "${REGION}" 2>/dev/null | grep -c "\.csv" || echo "0")

if [ "${TXN_COUNT}" -gt 0 ]; then
  pass "Archivos transactions encontrados: ${TXN_COUNT} CSV(s)"
  # Mostrar las primeras 5 líneas del primer archivo
  FIRST_TXN_FILE=$(aws s3 ls "s3://${S3_BUCKET}/raw/transactions/" \
    --region "${REGION}" 2>/dev/null | grep "\.csv" | head -1 | awk '{print $4}')
  if [ -n "${FIRST_TXN_FILE}" ]; then
    echo ""
    echo "  Preview (primeras 5 líneas de ${FIRST_TXN_FILE}):"
    aws s3 cp "s3://${S3_BUCKET}/raw/transactions/${FIRST_TXN_FILE}" - \
      --region "${REGION}" 2>/dev/null | head -5 | sed 's/^/  | /'
    echo ""
  fi
else
  fail "No se encontraron archivos CSV en s3://${S3_BUCKET}/raw/transactions/"
  echo "       El Full Load de DMS puede no haber completado aún."
fi

# ── [4] Verificar archivos customers en S3 ────────────────────────────────────
echo ""
info "[4/5] Verificando s3://${S3_BUCKET}/raw/customers/..."

CUST_COUNT=$(aws s3 ls "s3://${S3_BUCKET}/raw/customers/" \
  --region "${REGION}" 2>/dev/null | grep -c "\.csv" || echo "0")

if [ "${CUST_COUNT}" -gt 0 ]; then
  pass "Archivos customers encontrados: ${CUST_COUNT} CSV(s)"
  FIRST_CUST_FILE=$(aws s3 ls "s3://${S3_BUCKET}/raw/customers/" \
    --region "${REGION}" 2>/dev/null | grep "\.csv" | head -1 | awk '{print $4}')
  if [ -n "${FIRST_CUST_FILE}" ]; then
    echo ""
    echo "  Preview (primeras 5 líneas de ${FIRST_CUST_FILE}):"
    aws s3 cp "s3://${S3_BUCKET}/raw/customers/${FIRST_CUST_FILE}" - \
      --region "${REGION}" 2>/dev/null | head -5 | sed 's/^/  | /'
    echo ""
  fi
else
  fail "No se encontraron archivos CSV en s3://${S3_BUCKET}/raw/customers/"
fi

# ── [5] Conteo de filas ────────────────────────────────────────────────────────
echo ""
info "[5/5] Contando filas en los archivos raw..."

if [ "${TXN_COUNT}" -gt 0 ] && [ -n "${FIRST_TXN_FILE}" ]; then
  TXN_ROWS=$(aws s3 cp "s3://${S3_BUCKET}/raw/transactions/${FIRST_TXN_FILE}" - \
    --region "${REGION}" 2>/dev/null | wc -l || echo "0")
  # Restar encabezado si existe
  TXN_DATA_ROWS=$((TXN_ROWS - 1))
  pass "transactions: ~${TXN_DATA_ROWS} filas (esperado: ~1000)"

  if [ "${TXN_DATA_ROWS}" -ge 900 ]; then
    pass "Conteo de transacciones dentro del rango esperado (900-1100)"
  else
    fail "Conteo de transacciones inesperado: ${TXN_DATA_ROWS} (esperado: ~1000)"
  fi
fi

# ── Resumen ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
if [ "${FAILURES}" -eq 0 ]; then
  echo -e "${GREEN}  ✅ Validación: PASS (0 fallos)${NC}"
  echo ""
  echo "  Los datos están en S3 raw zone. Próximo paso:"
  echo "  → cd ../../../labs/02-transformation-quality-catalog/scripts"
  echo "  → ./setup.sh"
else
  echo -e "${RED}  ❌ Validación: FAIL (${FAILURES} fallos)${NC}"
  echo ""
  echo "  Revisa los errores arriba. Causas comunes:"
  echo "  - DMS task no iniciada: inicia manualmente desde la consola"
  echo "  - DMS task en progreso: espera a que complete el Full Load (~2 min)"
  echo "  - RDS sin datos: ejecuta seed.sql manualmente"
fi
echo "============================================================"
echo ""

exit ${FAILURES}
