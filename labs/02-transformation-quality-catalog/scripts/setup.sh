#!/usr/bin/env bash
# =============================================================================
# setup.sh — Lab 2: Transformation + Data Quality + Catalog
# =============================================================================
# Prerrequisito: Lab 1 debe estar deployed y con datos en S3 raw/
#
# Acciones:
#   1. Verifica que el stack del Lab 1 existe y tiene outputs
#   2. Obtiene el S3 bucket del Lab 1
#   3. Sube glue_job.py al bucket S3
#   4. Valida y despliega lab2-cfn.yaml
#   5. Muestra los outputs (GlueJobName, CrawlerName)
#   NO ejecuta el job — eso lo hace run_job.sh
#
# Uso:
#   ./setup.sh
#   o bien:
#   PROJECT_NAME=fincorp-datalake ENVIRONMENT=lab ./setup.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

PROJECT_NAME="${PROJECT_NAME:-fincorp-datalake}"
ENVIRONMENT="${ENVIRONMENT:-lab}"
REGION="${AWS_REGION:-us-east-1}"
LAB1_STACK="${PROJECT_NAME}-${ENVIRONMENT}-lab1"
LAB2_STACK="${PROJECT_NAME}-${ENVIRONMENT}-lab2"
GLUE_SCRIPT_KEY="scripts/glue_job.py"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
INFRA_DIR="${LAB_DIR}/infrastructure"

echo "============================================================"
echo "  FinCorp Data Platform — Lab 2: Transformation + DQ + Catalog"
echo "============================================================"
echo "  Stack Lab 1: ${LAB1_STACK}"
echo "  Stack Lab 2: ${LAB2_STACK}"
echo "  Región:      ${REGION}"
echo "============================================================"
echo ""

# ── [0] Prerequisitos ─────────────────────────────────────────────────────────
info "[0/5] Verificando prerequisitos..."
command -v aws >/dev/null 2>&1 || error "AWS CLI no encontrado."
command -v jq  >/dev/null 2>&1 || error "jq no encontrado."
aws sts get-caller-identity > /dev/null 2>&1 || error "No se pudo autenticar en AWS."
success "AWS CLI y credenciales OK"

# ── [1] Verificar Lab 1 stack ─────────────────────────────────────────────────
echo ""
info "[1/5] Verificando que el stack de Lab 1 existe..."

LAB1_STATUS=$(aws cloudformation describe-stacks \
  --stack-name "${LAB1_STACK}" \
  --region     "${REGION}" \
  --query      "Stacks[0].StackStatus" \
  --output     text 2>/dev/null || echo "NOT_FOUND")

if [ "${LAB1_STATUS}" = "NOT_FOUND" ]; then
  error "Stack Lab 1 (${LAB1_STACK}) no existe. Ejecuta primero: cd ../01-ingestion-data-lake/scripts && ./setup.sh"
fi

if [ "${LAB1_STATUS}" != "CREATE_COMPLETE" ] && [ "${LAB1_STATUS}" != "UPDATE_COMPLETE" ]; then
  error "Stack Lab 1 en estado inesperado: ${LAB1_STATUS}"
fi

success "Stack Lab 1: ${LAB1_STATUS}"

# Obtener S3 bucket del Lab 1
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "${LAB1_STACK}" \
  --region     "${REGION}" \
  --query      "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output     text)

[ -n "${S3_BUCKET}" ] || error "No se pudo obtener S3BucketName del Lab 1"
success "S3 Bucket (Lab 1): ${S3_BUCKET}"

# Verificar que hay datos en raw/
TXN_COUNT=$(aws s3 ls "s3://${S3_BUCKET}/raw/transactions/" \
  --region "${REGION}" 2>/dev/null | grep -c "\.csv" || echo "0")

if [ "${TXN_COUNT}" -eq 0 ]; then
  warn "No se encontraron CSVs en s3://${S3_BUCKET}/raw/transactions/"
  warn "El Glue job buscará también en staging/. Continúa si usaste ./setup.sh del Lab 1."
else
  success "Datos raw encontrados: ${TXN_COUNT} archivo(s) en raw/transactions/"
fi

# ── [2] Subir glue_job.py a S3 ───────────────────────────────────────────────
echo ""
info "[2/5] Subiendo glue_job.py a s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}..."

aws s3 cp "${LAB_DIR}/glue_job.py" \
  "s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}" \
  --region "${REGION}"

success "Script subido: s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}"

# ── [3] Validar template CloudFormation ──────────────────────────────────────
echo ""
info "[3/5] Validando template CloudFormation lab2-cfn.yaml..."

aws cloudformation validate-template \
  --template-body "file://${INFRA_DIR}/lab2-cfn.yaml" \
  --region        "${REGION}" \
  --output        text > /dev/null \
  || error "Template inválido. Revisa lab2-cfn.yaml."

success "Template válido"

# ── [4] Desplegar CloudFormation ──────────────────────────────────────────────
echo ""
info "[4/5] Desplegando stack Lab 2 (~2 min)..."

aws cloudformation deploy \
  --stack-name    "${LAB2_STACK}" \
  --template-file "${INFRA_DIR}/lab2-cfn.yaml" \
  --parameter-overrides \
    ProjectName="${PROJECT_NAME}" \
    Environment="${ENVIRONMENT}" \
    S3BucketName="${S3_BUCKET}" \
    GlueJobScriptS3Key="${GLUE_SCRIPT_KEY}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region        "${REGION}" \
  --no-fail-on-empty-changeset

success "Stack desplegado: ${LAB2_STACK}"

# ── [5] Mostrar outputs ───────────────────────────────────────────────────────
echo ""
info "[5/5] Outputs del stack..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "${LAB2_STACK}" \
    --region     "${REGION}" \
    --query      "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output     text
}

GLUE_JOB=$(get_output "GlueJobName")
CRAWLER=$(get_output "CrawlerName")
DB_NAME=$(get_output "GlueDatabaseName")

success "Glue Job    : ${GLUE_JOB}"
success "Crawler     : ${CRAWLER}"
success "Database    : ${DB_NAME}"

echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Lab 2 — Infraestructura lista${NC}"
echo "============================================================"
echo ""
echo "  Próximo paso — ejecutar el pipeline ETL:"
echo "     ./run_job.sh"
echo ""
echo "  Al terminar el lab:"
echo "     ./cleanup.sh"
echo ""
