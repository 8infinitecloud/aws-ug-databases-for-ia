#!/usr/bin/env bash
# =============================================================================
# setup.sh — Lab 1: Ingestion + Data Lake
# =============================================================================
# Despliega el data lake (S3) y la replicacion (DMS) desde el core transaccional.
# El sistema origen es infra/payments: este modulo NO crea base de datos.
#
# Tiempo estimado: ~15 minutos (DMS Replication Instance tarda ~10 min)
# Costo estimado:  ~$0.09/hr mientras los recursos están activos
#
# Uso:
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

# El sistema origen tiene que existir primero. Sin payments no hay Aurora de la
# que replicar, y el apply fallaria a mitad leyendo su state.
tf_init payments >/dev/null 2>&1 || true
if ! tf_deployed payments; then
  error "El core no esta desplegado. Ejecuta primero:
       cd infra/payments && terraform init -backend-config=../backend.hcl && terraform apply"
fi
success "Core de pagos desplegado (infra/payments)"

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text 2>/dev/null) \
  || error "No se pudo autenticar en AWS. Ejecuta: aws configure"

success "AWS Account: ${AWS_ACCOUNT} | Región: ${REGION}"

# ── [1/5] Inicializar Terraform ───────────────────────────────────────────────
echo ""
info "[1/5] Inicializando Terraform (modulo infra/lab1)..."

tf_init lab1 || error "terraform init fallo. Revisa infra/README.md."

success "Backend remoto conectado"

# ── [2/5] Aplicar ─────────────────────────────────────────────────────────────
echo ""
info "[2/5] Aplicando infraestructura (~15 min)..."
info "     DMS Replication Instance es el recurso más lento (~10 min). ☕"

# Este modulo ya no crea base de datos: el sistema origen es infra/payments, y
# las credenciales las lee de su Secrets Manager. Lo unico que necesita es saber
# donde vive el state remoto para poder leer el de payments.
TF_STATE_BUCKET=$(grep -E '^\s*bucket\s*=' "${TF_BACKEND_CONFIG}" | head -1 | sed 's/.*= *"\(.*\)"/\1/')
[ -n "${TF_STATE_BUCKET}" ] || error "No se pudo leer el bucket del state de ${TF_BACKEND_CONFIG}"

export TF_VAR_state_bucket="${TF_STATE_BUCKET}"
export TF_VAR_project_name="${PROJECT_NAME}"
export TF_VAR_environment="${ENVIRONMENT}"
export TF_VAR_aws_region="${REGION}"

tf_apply lab1 || error "terraform apply fallo."

success "Infraestructura aplicada"

# ── [3/5] Obtener outputs ─────────────────────────────────────────────────────
echo ""
info "[3/5] Leyendo outputs de Terraform..."

S3_BUCKET=$(tf_output lab1 s3_bucket_name)
DMS_TASK_ARN=$(tf_output lab1 dms_task_arn)
SOURCE_ENDPOINT=$(tf_output lab1 source_aurora_endpoint)
SOURCE_OBJECT=$(tf_output lab1 source_object)

[ -n "${S3_BUCKET}" ] || error "No se pudo leer s3_bucket_name. ¿El apply termino bien?"

success "Origen  : ${SOURCE_ENDPOINT}"
success "Objeto  : ${SOURCE_OBJECT}"
success "Data lake: s3://${S3_BUCKET}/raw/"

# ── [4/5] Subir dataset a S3 (zona staging) ──────────────────────────────────
echo ""
info "[4/5] Subiendo dataset al bucket S3 (zona staging/)..."

aws s3 cp "${DATASET_DIR}/transactions.csv" \
  "s3://${S3_BUCKET}/staging/transactions/transactions.csv" \
  --region "${REGION}"

aws s3 cp "${DATASET_DIR}/merchants.csv" \
  "s3://${S3_BUCKET}/staging/merchants/merchants.csv" \
  --region "${REGION}"

success "Dataset subido a s3://${S3_BUCKET}/staging/"

# ── [5/5] Recordatorio del schema del core ────────────────────────────────────
echo ""
info "[5/5] Schema del sistema origen"

# El schema ya NO se aplica aqui: vive en payments/schema/ y se aplica sobre
# Aurora, que es el sistema transaccional. Este modulo solo replica lo que ya
# esta ahi.
cat <<NOTA

  El origen de la replicacion es ${SOURCE_OBJECT} en Aurora.
  Si aun no has aplicado el schema del core:

    cd ../../../payments/schema
    # Aurora esta en subnets privadas: hace falta un tunel SSM o ejecutarlo
    # desde dentro de la VPC.
    for f in 00*.sql; do psql "\$CONN" -f "\$f"; done

  Sin datos en esa vista, la tarea DMS completara con 0 filas.

NOTA

# ── Resumen final ─────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Lab 1 — Infraestructura lista${NC}"
echo "============================================================"
echo ""
echo "  Origen      : ${SOURCE_ENDPOINT}"
echo "                ${SOURCE_OBJECT}  (vista del core, infra/payments)"
echo "  S3 Data Lake: s3://${S3_BUCKET}/"
echo "    ├── raw/          ← DMS escribe aquí (full load)"
echo "    ├── staging/      ← CSV subidos para carga manual"
echo "    └── curated/      ← Output del Lab 2 (Glue)"
echo ""
echo "  Próximos pasos:"
echo "  1. Aplica el schema del core si no lo has hecho (payments/schema/)"
echo "  2. Inicia la tarea DMS desde la consola AWS o con:"
echo "     aws dms start-replication-task --replication-task-arn ${DMS_TASK_ARN} --start-replication-task-type start-replication"
echo "  3. Valida el resultado con: ./validate.sh"
echo ""
echo "  Al terminar el lab:"
echo "     ./cleanup.sh"
echo ""
