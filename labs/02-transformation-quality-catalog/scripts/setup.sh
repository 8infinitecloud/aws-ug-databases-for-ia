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
#   4. Aplica el modulo Terraform infra/lab2
#   5. Muestra los outputs (glue_job_name, crawler_name)
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
GLUE_SCRIPT_KEY="scripts/glue_job.py"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "${LAB_DIR}/../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo "  FinCorp Data Platform — Lab 2: Transformation + DQ + Catalog"
echo "============================================================"
echo "  Modulos:  infra/lab1 (lectura) e infra/lab2"
echo "  Región:      ${REGION}"
echo "============================================================"
echo ""

# ── [0] Prerequisitos ─────────────────────────────────────────────────────────
info "[0/5] Verificando prerequisitos..."
command -v aws >/dev/null 2>&1 || error "AWS CLI no encontrado."
command -v jq  >/dev/null 2>&1 || error "jq no encontrado."
aws sts get-caller-identity > /dev/null 2>&1 || error "No se pudo autenticar en AWS."
tf_require || error "Terraform no esta listo. Ver infra/README.md."
success "AWS CLI, credenciales y Terraform OK"

# ── [1] Verificar Lab 1 ───────────────────────────────────────────────────────
echo ""
info "[1/5] Verificando que el Lab 1 esta desplegado..."

tf_init lab1 >/dev/null 2>&1 || true

if ! tf_deployed lab1; then
  error "El modulo infra/lab1 no tiene recursos. Ejecuta primero: cd ../../01-ingestion-data-lake/scripts && ./setup.sh"
fi

success "Modulo infra/lab1 desplegado"

S3_BUCKET=$(tf_output lab1 s3_bucket_name)
[ -n "${S3_BUCKET}" ] || error "No se pudo leer s3_bucket_name del Lab 1"
success "S3 Bucket (Lab 1): ${S3_BUCKET}"

# Verificar que hay datos en las dos fuentes de raw/
CORE=$(aws s3 ls "s3://${S3_BUCKET}/raw/acquiring/charges/" --recursive \
  --region "${REGION}" 2>/dev/null | grep -c "\.parquet" || true)
LEGACY=$(aws s3 ls "s3://${S3_BUCKET}/raw/legacy/transactions/" \
  --region "${REGION}" 2>/dev/null | grep -c "\.csv" || true)

if [ "${CORE}" -eq 0 ]; then
  warn "Sin datos del core en raw/acquiring/: ¿la tarea DMS del Lab 1 esta en running?"
  warn "El job correra solo con la cartera legacy."
else
  success "Core: ${CORE} fichero(s) Parquet de DMS en raw/acquiring/charges/"
fi
if [ "${LEGACY}" -eq 0 ]; then
  warn "Sin cartera legacy en raw/legacy/: vuelve a ejecutar el setup.sh del Lab 1."
else
  success "Legacy: raw/legacy/transactions/"
fi

# ── [2] Subir glue_job.py a S3 ───────────────────────────────────────────────
echo ""
info "[2/5] Subiendo glue_job.py a s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}..."

aws s3 cp "${LAB_DIR}/glue_job.py" \
  "s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}" \
  --region "${REGION}"

success "Script subido: s3://${S3_BUCKET}/${GLUE_SCRIPT_KEY}"

# ── [3] Inicializar Terraform ─────────────────────────────────────────────────
echo ""
info "[3/5] Inicializando Terraform (modulo infra/lab2)..."

tf_init lab2 || error "terraform init fallo. Revisa infra/README.md."

success "Backend remoto conectado"

# ── [4] Aplicar ───────────────────────────────────────────────────────────────
echo ""
info "[4/5] Aplicando infraestructura del Lab 2 (~2 min)..."

# El bucket NO se pasa: el modulo lo lee del state del Lab 1 con
# terraform_remote_state. Solo hace falta decirle donde vive ese state.
TF_STATE_BUCKET=$(grep -E '^\s*bucket\s*=' "${TF_BACKEND_CONFIG}" | head -1 | sed 's/.*= *"\(.*\)"/\1/')
[ -n "${TF_STATE_BUCKET}" ] || error "No se pudo leer el bucket del state de ${TF_BACKEND_CONFIG}"

export TF_VAR_state_bucket="${TF_STATE_BUCKET}"
export TF_VAR_project_name="${PROJECT_NAME}"
export TF_VAR_environment="${ENVIRONMENT}"
export TF_VAR_aws_region="${REGION}"
export TF_VAR_glue_job_script_s3_key="${GLUE_SCRIPT_KEY}"

tf_apply lab2 || error "terraform apply fallo."

success "Infraestructura del Lab 2 aplicada"

# ── [5] Mostrar outputs ───────────────────────────────────────────────────────
echo ""
info "[5/5] Outputs de Terraform..."

get_output() { tf_output lab2 "$1"; }

GLUE_JOB=$(get_output glue_job_name)
CRAWLER=$(get_output crawler_name)
DB_NAME=$(get_output glue_database_name)

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
