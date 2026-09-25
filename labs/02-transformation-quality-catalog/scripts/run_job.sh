#!/usr/bin/env bash
# =============================================================================
# run_job.sh — Lab 2: Ejecutar Glue Job + Crawler + mostrar Quality Report
# =============================================================================
# Acciones:
#   1. Inicia el Glue job y hace polling hasta que completa (~3 min)
#   2. Descarga y muestra quality_report.json
#   3. Inicia el Glue Crawler para actualizar el Catalog
#   4. Muestra las tablas descubiertas en el Glue Catalog
#
# Uso: ./run_job.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }
header()  { echo -e "\n${CYAN}━━━ $* ━━━${NC}"; }

PROJECT_NAME="${PROJECT_NAME:-fincorp-datalake}"
ENVIRONMENT="${ENVIRONMENT:-lab}"
REGION="${AWS_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
POLL_INTERVAL=15  # segundos entre cada check del job status

get_lab2_output() { tf_output lab2 "$1"; }
get_lab1_output() { tf_output lab1 "$1"; }

echo "============================================================"
echo "  Lab 2 — Ejecutar ETL Pipeline"
echo "============================================================"
echo ""

# Obtener nombres de los recursos
GLUE_JOB=$(get_lab2_output glue_job_name)
CRAWLER=$(get_lab2_output crawler_name)
DB_NAME=$(get_lab2_output glue_database_name)
S3_BUCKET=$(get_lab1_output s3_bucket_name)

[ -n "${GLUE_JOB}" ]   || error "glue_job_name no encontrado. ¿Ejecutaste setup.sh primero?"
[ -n "${CRAWLER}" ]    || error "crawler_name no encontrado."
[ -n "${S3_BUCKET}" ]  || error "s3_bucket_name no encontrado en el modulo del Lab 1."

info "Glue Job : ${GLUE_JOB}"
info "Crawler  : ${CRAWLER}"
info "S3 Bucket: ${S3_BUCKET}"

# ── [1] Iniciar Glue Job ──────────────────────────────────────────────────────
header "PASO 1/4: Iniciar Glue Job"

RUN_ID=$(aws glue start-job-run \
  --job-name "${GLUE_JOB}" \
  --region   "${REGION}" \
  --query    "JobRunId" \
  --output   text)

success "Job iniciado — Run ID: ${RUN_ID}"
info "Polling cada ${POLL_INTERVAL}s (timeout: 10 min)..."

# ── Polling del job ───────────────────────────────────────────────────────────
ELAPSED=0
MAX_WAIT=600  # 10 minutos

while [ "${ELAPSED}" -lt "${MAX_WAIT}" ]; do
  sleep "${POLL_INTERVAL}"
  ELAPSED=$((ELAPSED + POLL_INTERVAL))

  JOB_STATE=$(aws glue get-job-run \
    --job-name  "${GLUE_JOB}" \
    --run-id    "${RUN_ID}" \
    --region    "${REGION}" \
    --query     "JobRun.JobRunState" \
    --output    text 2>/dev/null || echo "UNKNOWN")

  case "${JOB_STATE}" in
    SUCCEEDED)
      echo ""
      success "Job completado en ~${ELAPSED}s"
      break
      ;;
    FAILED|ERROR|TIMEOUT)
      echo ""
      error "Job falló con estado: ${JOB_STATE}"
      echo "  Logs en CloudWatch: /aws-glue/jobs/${PROJECT_NAME}-${ENVIRONMENT}"
      aws glue get-job-run \
        --job-name "${GLUE_JOB}" \
        --run-id   "${RUN_ID}" \
        --region   "${REGION}" \
        --query    "JobRun.ErrorMessage" \
        --output   text 2>/dev/null || true
      exit 1
      ;;
    RUNNING|STARTING)
      echo -n "  [${ELAPSED}s] ${JOB_STATE}..."
      ;;
    *)
      echo -n "  [${ELAPSED}s] ${JOB_STATE}..."
      ;;
  esac
done
echo ""

if [ "${ELAPSED}" -ge "${MAX_WAIT}" ]; then
  error "Timeout esperando el job (${MAX_WAIT}s). Revisa CloudWatch."
fi

# ── [2] Mostrar Quality Report ────────────────────────────────────────────────
header "PASO 2/4: Quality Report"

REPORT_KEY="quality-reports/latest/quality_report.json"

REPORT_JSON=$(aws s3 cp "s3://${S3_BUCKET}/${REPORT_KEY}" - \
  --region "${REGION}" 2>/dev/null || echo "{}")

if [ "${REPORT_JSON}" = "{}" ]; then
  warn "No se encontró el quality report en s3://${S3_BUCKET}/${REPORT_KEY}"
else
  echo ""
  echo "  ┌─────────────────────────────────────────────────────┐"
  echo "  │           REPORTE DE CALIDAD DE DATOS               │"
  echo "  └─────────────────────────────────────────────────────┘"
  echo ""

  # Extraer métricas clave con jq
  if command -v jq >/dev/null 2>&1; then
    TOTAL=$(echo "${REPORT_JSON}" | jq -r '.data_quality.total_rows // "N/A"')
    VALID=$(echo "${REPORT_JSON}" | jq -r '.data_quality.valid_rows // "N/A"')
    REJECTED=$(echo "${REPORT_JSON}" | jq -r '.data_quality.rejected_rows // "N/A"')
    SCORE=$(echo "${REPORT_JSON}" | jq -r '.summary.overall_dq_score // "N/A"')
    RULES=$(echo "${REPORT_JSON}" | jq -r '.summary.rules_passed // "N/A"')
    REC=$(echo "${REPORT_JSON}" | jq -r '.summary.recommendation // "N/A"')

    echo "  Filas procesadas : ${TOTAL}"
    echo "  Filas válidas    : ${VALID}"
    echo "  Filas rechazadas : ${REJECTED}"
    echo "  Score DQ         : ${SCORE}"
    echo "  Reglas pasadas   : ${RULES}"
    echo "  Recomendación    : ${REC}"
    echo ""

    # Detalle por regla
    echo "  Detalle de reglas:"
    echo "${REPORT_JSON}" | jq -r '
      .data_quality.rules | to_entries[] |
      "  " + (if .value.passed then "✓" else "✗" end) +
      " " + .key +
      " → score: " + (.value.score | tostring) +
      (if .value.failed_rows > 0 then " (" + (.value.failed_rows | tostring) + " filas)" else "" end)
    ' 2>/dev/null || echo "${REPORT_JSON}" | python3 -m json.tool
  else
    echo "${REPORT_JSON}" | python3 -m json.tool
  fi
fi

# ── [3] Iniciar Crawler ───────────────────────────────────────────────────────
header "PASO 3/4: Iniciar Glue Crawler"

CRAWLER_STATUS=$(aws glue get-crawler \
  --name   "${CRAWLER}" \
  --region "${REGION}" \
  --query  "Crawler.State" \
  --output text 2>/dev/null || echo "UNKNOWN")

if [ "${CRAWLER_STATUS}" = "RUNNING" ]; then
  warn "Crawler ya está corriendo. Esperando..."
else
  aws glue start-crawler \
    --name   "${CRAWLER}" \
    --region "${REGION}" \
    && success "Crawler iniciado: ${CRAWLER}"
fi

# Polling del crawler
ELAPSED=0
MAX_CRAWLER_WAIT=300  # 5 minutos

while [ "${ELAPSED}" -lt "${MAX_CRAWLER_WAIT}" ]; do
  sleep 10
  ELAPSED=$((ELAPSED + 10))

  CRAWLER_STATUS=$(aws glue get-crawler \
    --name   "${CRAWLER}" \
    --region "${REGION}" \
    --query  "Crawler.State" \
    --output text 2>/dev/null || echo "UNKNOWN")

  if [ "${CRAWLER_STATUS}" = "READY" ]; then
    success "Crawler completado en ~${ELAPSED}s"
    break
  fi
  echo -n "  [${ELAPSED}s] Crawler: ${CRAWLER_STATUS}..."
done
echo ""

# ── [4] Mostrar tablas en Glue Catalog ────────────────────────────────────────
header "PASO 4/4: Tablas en Glue Data Catalog"

TABLES=$(aws glue get-tables \
  --database-name "${DB_NAME}" \
  --region        "${REGION}" \
  --query         "TableList[*].[Name,StorageDescriptor.Location,Parameters.recordCount]" \
  --output        table 2>/dev/null || echo "  (sin tablas aún)")

echo ""
echo "  Database: ${DB_NAME}"
echo "${TABLES}"

echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Pipeline ETL completado${NC}"
echo "============================================================"
echo ""
echo "  Datos curados en: s3://${S3_BUCKET}/curated/transactions/"
echo "  Tabla en Catalog: ${DB_NAME}.transactions_curated"
echo ""
echo "  Para consultar con Athena:"
echo "  SELECT COUNT(*), transaction_type, status"
echo "  FROM \"${DB_NAME}\".\"transactions_curated\""
echo "  GROUP BY transaction_type, status"
echo ""
echo "  Próximo paso: Lab 3"
echo "  → cd ../../03-data-to-ai/infrastructure && ./setup.sh"
echo ""
