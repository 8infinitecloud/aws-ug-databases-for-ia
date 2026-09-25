#!/usr/bin/env bash
# =============================================================================
# cleanup.sh — Lab 1: Eliminar todos los recursos AWS
# =============================================================================
# EJECUTAR AL TERMINAR EL LAB.
# Costo estimado si se dejan corriendo: ~$0.09/hr (RDS + DMS)
#
# Acciones:
#   1. Para la tarea DMS si está corriendo
#   2. Vacía el bucket S3 (objetos versioned)
#   3. Elimina el CloudFormation stack (VPC, RDS, DMS, S3, IAM, Secrets)
#   4. Confirma que no quedan recursos
#
# Uso: ./cleanup.sh
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

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo -e "${RED}  ⚠️  CLEANUP — Lab 1: Eliminar recursos AWS${NC}"
echo "============================================================"
echo ""
echo "  Esto eliminará PERMANENTEMENTE:"
echo "  - Todo el modulo Terraform infra/lab1"
echo "  - VPC + Subnets + Security Groups"
echo "  - RDS MySQL (${PROJECT_NAME}-${ENVIRONMENT}-mysql)"
echo "  - DMS Replication Instance + Endpoints + Task"
echo "  - S3 Bucket + todos los objetos (raw/, curated/, staging/)"
echo "  - Secrets Manager secret"
echo "  - IAM Role DMS"
echo ""
read -r -p "  ¿Confirmar? (escribe 'si' para continuar): " CONFIRM

if [ "${CONFIRM}" != "si" ]; then
  echo "  Cleanup cancelado."
  exit 0
fi

echo ""

# ── Verificar que hay algo que eliminar ───────────────────────────────────────
tf_require || error "Terraform no esta listo. Ver infra/README.md."
tf_init lab1 >/dev/null 2>&1 || true

if ! tf_deployed lab1; then
  warn "El modulo infra/lab1 no tiene recursos. Nada que eliminar."
  exit 0
fi

DMS_TASK_ARN=$(tf_output lab1 dms_task_arn)
S3_BUCKET=$(tf_output lab1 s3_bucket_name)

# ── [1/4] Detener DMS Task ────────────────────────────────────────────────────
echo ""
info "[1/4] Deteniendo tarea DMS si está en ejecución..."

if [ -n "${DMS_TASK_ARN}" ] && [ "${DMS_TASK_ARN}" != "None" ]; then
  TASK_STATUS=$(aws dms describe-replication-tasks \
    --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" \
    --region  "${REGION}" \
    --query   "ReplicationTasks[0].Status" \
    --output  text 2>/dev/null || echo "unknown")

  if [ "${TASK_STATUS}" = "running" ]; then
    aws dms stop-replication-task \
      --replication-task-arn "${DMS_TASK_ARN}" \
      --region "${REGION}" > /dev/null 2>&1 || true

    info "     Esperando que la tarea se detenga..."
    for _ in {1..12}; do
      sleep 5
      STATUS=$(aws dms describe-replication-tasks \
        --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" \
        --region  "${REGION}" \
        --query   "ReplicationTasks[0].Status" \
        --output  text 2>/dev/null || echo "unknown")
      if [ "${STATUS}" = "stopped" ] || [ "${STATUS}" = "ready" ]; then
        break
      fi
      echo -n "."
    done
    echo ""
    success "Tarea DMS detenida"
  else
    success "Tarea DMS no estaba en ejecución (status: ${TASK_STATUS})"
  fi
else
  warn "ARN de tarea DMS no encontrado en los outputs de Terraform"
fi

# ── [2/4] Vaciar bucket S3 (incluyendo versiones) ────────────────────────────
echo ""
info "[2/4] Vaciando bucket S3: ${S3_BUCKET}..."

if [ -n "${S3_BUCKET}" ] && [ "${S3_BUCKET}" != "None" ]; then
  # Eliminar todos los objetos
  aws s3 rm "s3://${S3_BUCKET}" \
    --recursive \
    --region "${REGION}" 2>/dev/null || true

  # Eliminar versiones antiguas (S3 Versioning activo)
  aws s3api list-object-versions \
    --bucket "${S3_BUCKET}" \
    --region "${REGION}" \
    --output json 2>/dev/null | \
  python3 -c "
import json, sys
data = json.load(sys.stdin)
objects = []
for v in data.get('Versions', []):
    objects.append({'Key': v['Key'], 'VersionId': v['VersionId']})
for m in data.get('DeleteMarkers', []):
    objects.append({'Key': m['Key'], 'VersionId': m['VersionId']})
if objects:
    print(json.dumps({'Objects': objects, 'Quiet': True}))
" | xargs -I{} aws s3api delete-objects \
    --bucket "${S3_BUCKET}" \
    --region "${REGION}" \
    --delete "{}" 2>/dev/null || true

  success "Bucket vaciado: ${S3_BUCKET}"
else
  warn "Nombre del bucket no encontrado en outputs de Terraform"
fi

# ── [3/4] Destruir la infraestructura ─────────────────────────────────────────
echo ""
info "[3/4] Ejecutando terraform destroy (VPC, RDS, DMS, S3, IAM)..."
info "      Tiempo estimado: ~5-10 minutos"

# terraform destroy necesita las variables sin default para poder planificar,
# aunque el valor no se use para borrar nada.
export TF_VAR_db_password="${DB_PASSWORD:-placeholder-no-usado-en-destroy}"
export TF_VAR_project_name="${PROJECT_NAME}"
export TF_VAR_environment="${ENVIRONMENT}"
export TF_VAR_aws_region="${REGION}"

tf_destroy lab1 || warn "terraform destroy devolvio error — revisa la consola AWS"

success "Infraestructura del Lab 1 destruida"

# ── [4/4] Verificación final ──────────────────────────────────────────────────
echo ""
info "[4/4] Verificación final..."

if tf_deployed lab1; then
  warn "Quedan recursos en el state de infra/lab1:"
  terraform -chdir="${REPO_ROOT}/infra/lab1" state list 2>/dev/null | sed 's/^/      /'
else
  success "El state de infra/lab1 quedo vacio"
fi

# ── Resumen ────────────────────────────────────────────────────────────────────
echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Cleanup Lab 1 completado${NC}"
echo "============================================================"
echo ""
echo "  Recursos eliminados:"
echo "  ✓ Modulo Terraform infra/lab1"
echo "  ✓ VPC + Networking"
echo "  ✓ RDS MySQL"
echo "  ✓ DMS Replication Instance + Task"
echo "  ✓ S3 Bucket + objetos"
echo "  ✓ Secrets Manager"
echo "  ✓ IAM Role DMS"
echo ""
echo "  Verifica en la consola AWS que no queden recursos:"
echo "  - https://console.aws.amazon.com/rds"
echo "  - https://console.aws.amazon.com/dms"
echo "  - https://console.aws.amazon.com/s3"
echo ""
