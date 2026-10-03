#!/usr/bin/env bash
# =============================================================================
# setup.sh — Lab 1: Ingestion + Data Lake
# =============================================================================
# Despliega el data lake (S3) y la replicacion (DMS con CDC) desde el core
# transaccional, y la arranca. El sistema origen es el PostgreSQL de
# infra/payments: este modulo NO crea base de datos.
#
# Tiempo estimado: ~15 minutos (DMS Replication Instance tarda ~10 min)
# Costo estimado:  ~$0.04/hr mientras la replicacion esta activa (dms.t3.small)
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

# El sistema origen tiene que existir primero. Sin payments no hay base de la
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
SOURCE_HOST=$(tf_output lab1 source_db_host)

[ -n "${S3_BUCKET}" ] || error "No se pudo leer s3_bucket_name. ¿El apply termino bien?"

success "Origen   : PostgreSQL del core en ${SOURCE_HOST} (EC2, infra/payments)"
success "Data lake: s3://${S3_BUCKET}/raw/"

# ── [4/5] La cartera legacy, como fichero ────────────────────────────────────
# El sistema del adquirente absorbido no tiene DMS: su historia llega como un
# volcado, sucia, y se deja tal cual en raw/legacy/. El Lab 2 la une a la del
# core y es donde la calidad tiene algo que encontrar.
echo ""
info "[4/5] Subiendo la cartera legacy a raw/legacy/..."

aws s3 cp "${DATASET_DIR}/transactions.csv" \
  "s3://${S3_BUCKET}/raw/legacy/transactions/transactions.csv" --region "${REGION}"
aws s3 cp "${DATASET_DIR}/merchants.csv" \
  "s3://${S3_BUCKET}/raw/legacy/merchants/merchants.csv" --region "${REGION}"

success "Legacy en s3://${S3_BUCKET}/raw/legacy/"

# ── [5/5] Arrancar la replicacion ────────────────────────────────────────────
# Desde aqui el core tiene un slot de replicacion que retiene WAL hasta que DMS
# lo consume. Por eso no arranca solo con el apply, y por eso cleanup.sh borra
# el slot al terminar.
echo ""
info "[5/5] Arrancando la tarea DMS (full load + CDC)..."

estado_tarea() {
  aws dms describe-replication-tasks \
    --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" \
    --region "${REGION}" --query "ReplicationTasks[0].Status" --output text 2>/dev/null
}

ESTADO=$(estado_tarea)
case "${ESTADO}" in
  ready)   TIPO="start-replication" ;;
  stopped) TIPO="resume-processing" ;;   # retoma el CDC donde lo dejo
  running) TIPO="" ;;
  *)       error "La tarea esta en estado ${ESTADO}: revisala en la consola de DMS." ;;
esac

if [ -n "${TIPO}" ]; then
  aws dms start-replication-task --replication-task-arn "${DMS_TASK_ARN}" \
    --start-replication-task-type "${TIPO}" --region "${REGION}" >/dev/null
  info "     Esperando la foto inicial (full load)..."
  for _ in $(seq 1 60); do
    sleep 10
    ESTADO=$(estado_tarea)
    PCT=$(aws dms describe-replication-tasks \
      --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" --region "${REGION}" \
      --query "ReplicationTasks[0].ReplicationTaskStats.FullLoadProgressPercent" --output text)
    echo -n " ${PCT}%"
    [ "${ESTADO}" = "running" ] && [ "${PCT}" = "100" ] && break
    [ "${ESTADO}" = "failed" ] && { echo; error "La tarea fallo. Log: /aws/dms/tasks/<id> en CloudWatch."; }
  done
  echo ""
fi
success "Replicando: foto inicial completa y CDC escuchando el WAL"

# ── Resumen final ─────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Lab 1 — Replicacion en marcha${NC}"
echo "============================================================"
echo ""
echo "  s3://${S3_BUCKET}/"
echo "    raw/acquiring/<tabla>/LOAD*.parquet          la foto inicial"
echo "    raw/acquiring/<tabla>/AAAA/MM/DD/*.parquet   cada cambio, cada ~30 s"
echo "    raw/legacy/                                  la cartera absorbida"
echo ""
echo "  Haz un pago (el POS, la web del QR o el generador) y mira como llega:"
echo "     python scripts/generate_traffic.py --api \$(terraform -chdir=infra/payments output -raw api_endpoint) --count 20"
echo "     ./validate.sh"
echo ""
echo "  Al terminar el lab (borra tambien el slot del core):"
echo "     ./cleanup.sh"
echo ""
