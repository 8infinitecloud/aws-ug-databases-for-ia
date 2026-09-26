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
#   export TF_VAR_db_password="TuPassword123!"
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "${LAB_DIR}/../.." && pwd)"
DATASET_DIR="${LAB_DIR}/dataset"

# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo "  FinCorp Data Platform — Lab 1: Ingestion + Data Lake"
echo "============================================================"
echo "  Modulo:  infra/lab1"
echo "  Región:  ${REGION}"
echo "  Env:     ${ENVIRONMENT}"
echo "============================================================"
echo ""

# ── [0/5] Prerequisitos ───────────────────────────────────────────────────────
info "[0/5] Verificando prerequisitos..."

command -v aws >/dev/null 2>&1 || error "AWS CLI no encontrado. Instala: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
command -v jq  >/dev/null 2>&1 || error "jq no encontrado. Instala: brew install jq (macOS) / apt install jq (Linux)"
tf_require || error "Terraform no esta listo. Ver infra/README.md."

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || error "No se pudo autenticar en AWS. Ejecuta: aws configure"

success "AWS Account: ${AWS_ACCOUNT} | Región: ${REGION}"

# La guia del workshop pide exportar TF_VAR_db_password, que es lo que Terraform
# lee directamente. DB_PASSWORD se acepta por compatibilidad.
DB_PASSWORD="${DB_PASSWORD:-${TF_VAR_db_password:-}}"

if [ -z "${DB_PASSWORD}" ]; then
  echo ""
  warn "Ni TF_VAR_db_password ni DB_PASSWORD estan definidas."
  read -r -s -p "  Ingresa la contraseña para RDS MySQL (mín. 8 caracteres): " DB_PASSWORD
  echo ""
  [ ${#DB_PASSWORD} -ge 8 ] || error "La contraseña debe tener al menos 8 caracteres."
fi

# ── [1/5] Inicializar Terraform ───────────────────────────────────────────────
echo ""
info "[1/5] Inicializando Terraform (modulo infra/lab1)..."

tf_init lab1 || error "terraform init fallo. Revisa infra/README.md."

success "Backend remoto conectado"

# ── [2/5] Aplicar ─────────────────────────────────────────────────────────────
echo ""
info "[2/5] Aplicando infraestructura (~15 min)..."
info "     DMS Replication Instance es el recurso más lento (~10 min). ☕"

# La contraseña viaja por TF_VAR_, nunca como argumento de linea de comandos:
# -var la dejaria visible en el historial del shell y en la lista de procesos.
export TF_VAR_db_password="${DB_PASSWORD}"
export TF_VAR_project_name="${PROJECT_NAME}"
export TF_VAR_environment="${ENVIRONMENT}"
export TF_VAR_db_username="${DB_USERNAME}"
export TF_VAR_db_name="${DB_NAME}"
export TF_VAR_aws_region="${REGION}"

tf_apply lab1 || error "terraform apply fallo."

success "Infraestructura aplicada"

# ── [3/5] Obtener outputs ─────────────────────────────────────────────────────
echo ""
info "[3/5] Leyendo outputs de Terraform..."

RDS_ENDPOINT=$(tf_output lab1 rds_endpoint)
S3_BUCKET=$(tf_output lab1 s3_bucket_name)
SECRET_ARN=$(tf_output lab1 secret_arn)
DMS_TASK_ARN=$(tf_output lab1 dms_task_arn)

[ -n "${S3_BUCKET}" ] || error "No se pudo leer s3_bucket_name. ¿El apply termino bien?"

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
