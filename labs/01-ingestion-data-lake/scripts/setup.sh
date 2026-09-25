#!/usr/bin/env bash
# =============================================================================
# setup.sh — Lab 1: Ingestion + Data Lake
# =============================================================================
# Despliega el stack CloudFormation de Lab 1 (VPC, RDS MySQL, DMS, S3)
# y siembra la base de datos con el schema y datos de ejemplo.
#
# Tiempo estimado: ~15 minutos (DMS Replication Instance tarda ~10 min)
# Costo estimado:  ~$0.09/hr mientras los recursos están activos
#
# Uso:
#   export DB_PASSWORD="TuPassword123!"
#   ./setup.sh
#
#   O con todas las variables:
#   PROJECT_NAME=fincorp-datalake ENVIRONMENT=lab AWS_REGION=us-east-1 ./setup.sh
# =============================================================================

set -euo pipefail

# ── Colores para output ────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

# ── Variables ─────────────────────────────────────────────────────────────────
PROJECT_NAME="${PROJECT_NAME:-fincorp-datalake}"
ENVIRONMENT="${ENVIRONMENT:-lab}"
REGION="${AWS_REGION:-us-east-1}"
DB_USERNAME="${DB_USERNAME:-fincorpadmin}"
DB_NAME="${DB_NAME:-fincorp}"
STACK_NAME="${PROJECT_NAME}-${ENVIRONMENT}-lab1"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
INFRA_DIR="${LAB_DIR}/infrastructure"
DATASET_DIR="${LAB_DIR}/dataset"

echo "============================================================"
echo "  FinCorp Data Platform — Lab 1: Ingestion + Data Lake"
echo "============================================================"
echo "  Stack:   ${STACK_NAME}"
echo "  Región:  ${REGION}"
echo "  Env:     ${ENVIRONMENT}"
echo "============================================================"
echo ""

# ── [0/5] Prerequisitos ───────────────────────────────────────────────────────
info "[0/5] Verificando prerequisitos..."

command -v aws >/dev/null 2>&1 || error "AWS CLI no encontrado. Instala: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
command -v jq  >/dev/null 2>&1 || error "jq no encontrado. Instala: brew install jq (macOS) / apt install jq (Linux)"

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || error "No se pudo autenticar en AWS. Ejecuta: aws configure"

success "AWS Account: ${AWS_ACCOUNT} | Región: ${REGION}"

# Verificar/solicitar DB_PASSWORD
if [ -z "${DB_PASSWORD:-}" ]; then
  echo ""
  warn "DB_PASSWORD no definida como variable de entorno."
  read -r -s -p "  Ingresa la contraseña para RDS MySQL (mín. 8 caracteres): " DB_PASSWORD
  echo ""
  [ ${#DB_PASSWORD} -ge 8 ] || error "La contraseña debe tener al menos 8 caracteres."
fi

# ── [1/5] Validar template CloudFormation ─────────────────────────────────────
echo ""
info "[1/5] Validando template CloudFormation..."

aws cloudformation validate-template \
  --template-body "file://${INFRA_DIR}/lab1-cfn.yaml" \
  --region "${REGION}" \
  --output text > /dev/null \
  || error "Template inválido. Revisa lab1-cfn.yaml con cfn-lint."

success "Template válido"

# ── [2/5] Desplegar CloudFormation ────────────────────────────────────────────
echo ""
info "[2/5] Desplegando CloudFormation stack (~15 min)..."
info "     DMS Replication Instance es el recurso más lento (~10 min). ☕"

aws cloudformation deploy \
  --stack-name    "${STACK_NAME}" \
  --template-file "${INFRA_DIR}/lab1-cfn.yaml" \
  --parameter-overrides \
    ProjectName="${PROJECT_NAME}" \
    Environment="${ENVIRONMENT}" \
    DBPassword="${DB_PASSWORD}" \
    DBUsername="${DB_USERNAME}" \
    DBName="${DB_NAME}" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region        "${REGION}" \
  --no-fail-on-empty-changeset

success "Stack desplegado: ${STACK_NAME}"

# ── [3/5] Obtener outputs ─────────────────────────────────────────────────────
echo ""
info "[3/5] Leyendo outputs del stack..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "${STACK_NAME}" \
    --region     "${REGION}" \
    --query      "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output     text
}

RDS_ENDPOINT=$(get_output "RDSEndpoint")
S3_BUCKET=$(get_output "S3BucketName")
SECRET_ARN=$(get_output "SecretArn")
DMS_TASK_ARN=$(get_output "DMSTaskArn")

success "RDS Endpoint : ${RDS_ENDPOINT}"
success "S3 Bucket    : ${S3_BUCKET}"
success "Secret ARN   : ${SECRET_ARN}"

# ── [4/5] Subir dataset a S3 (zona staging) ──────────────────────────────────
echo ""
info "[4/5] Subiendo dataset al bucket S3 (zona staging/)..."

aws s3 cp "${DATASET_DIR}/transactions.csv" \
  "s3://${S3_BUCKET}/staging/transactions/transactions.csv" \
  --region "${REGION}"

aws s3 cp "${DATASET_DIR}/customers.csv" \
  "s3://${S3_BUCKET}/staging/customers/customers.csv" \
  --region "${REGION}"

success "Dataset subido a s3://${S3_BUCKET}/staging/"

# ── [5/5] Sembrar RDS ─────────────────────────────────────────────────────────
echo ""
info "[5/5] Sembrando RDS MySQL..."

if command -v mysql >/dev/null 2>&1; then
  info "     Ejecutando seed.sql en RDS..."
  # Nota: RDS está en subnet privada. Si corres este script desde fuera de la VPC,
  # necesitas un Bastion Host o AWS Systems Manager Session Manager.
  # Para el lab, el setup asume que tienes conectividad (VPN, Bastion, o Cloud9).
  mysql \
    -h "${RDS_ENDPOINT}" \
    -u "${DB_USERNAME}" \
    -p"${DB_PASSWORD}" \
    --connect-timeout=10 \
    < "${DATASET_DIR}/seed.sql" \
    && success "seed.sql ejecutado correctamente" \
    || warn "No se pudo conectar a RDS. Ejecuta seed.sql manualmente una vez que tengas conectividad a la VPC."
else
  warn "mysql client no encontrado."
  echo ""
  echo "  Para sembrar la BD manualmente:"
  echo "  1. Crea un Bastion Host EC2 en la subnet pública del stack"
  echo "  2. Copia seed.sql al Bastion"
  echo "  3. Ejecuta: mysql -h ${RDS_ENDPOINT} -u ${DB_USERNAME} -p < seed.sql"
  echo ""
  echo "  O usa AWS Cloud9 / SSM Session Manager si tienes un bastion."
fi

# ── Resumen final ─────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Lab 1 — Infraestructura lista${NC}"
echo "============================================================"
echo ""
echo "  RDS MySQL   : ${RDS_ENDPOINT}:3306/${DB_NAME}"
echo "  S3 Data Lake: s3://${S3_BUCKET}/"
echo "    ├── raw/          ← DMS escribe aquí (Full Load + CDC)"
echo "    ├── staging/      ← CSV subidos para carga manual"
echo "    └── curated/      ← Output del Lab 2 (Glue)"
echo ""
echo "  Próximos pasos:"
echo "  1. Verifica conectividad a RDS y ejecuta seed.sql si no se ejecutó automáticamente"
echo "  2. Inicia la tarea DMS desde la consola AWS o con:"
echo "     aws dms start-replication-task --replication-task-arn ${DMS_TASK_ARN} --start-replication-task-type start-replication"
echo "  3. Valida el resultado con: ./validate.sh"
echo ""
echo "  Al terminar el lab:"
echo "     ./cleanup.sh"
echo ""
