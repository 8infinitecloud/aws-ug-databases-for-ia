#!/usr/bin/env bash
# =============================================================================
# cleanup.sh — Lab 2: Eliminar recursos Glue y datos curados
# =============================================================================
# Costo del Lab 2 si se deja corriendo: ~$0 (Glue no tiene costo en idle)
# Solo cobra cuando el job corre: ~$0.0006 por ejecución de 3 min
#
# Acciones:
#   1. Elimina tablas del Glue Catalog (financial_platform)
#   2. Elimina curated/, rejected/ y quality-reports/ del S3
#   3. Destruye el modulo Terraform del Lab 2
#   NOTA: NO elimina el bucket S3 ni los datos raw/ (pertenecen al Lab 1)
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
echo -e "${RED}  ⚠️  CLEANUP — Lab 2: Eliminar recursos Glue${NC}"
echo "============================================================"
echo ""
echo "  Esto eliminará:"
echo "  - Todo el modulo Terraform infra/lab2"
echo "  - Glue Job, Crawler, Database, DQ Ruleset"
echo "  - IAM Role Glue"
echo "  - S3: curated/, rejected/, quality-reports/ (datos procesados)"
echo ""
echo "  NO se elimina:"
echo "  - S3 bucket (pertenece al Lab 1)"
echo "  - S3: raw/ (datos originales del Lab 1)"
echo ""
read -r -p "  ¿Confirmar? (escribe 'si' para continuar): " CONFIRM

if [ "${CONFIRM}" != "si" ]; then
  echo "  Cleanup cancelado."
  exit 0
fi

echo ""

# Obtener S3 bucket del Lab 1 (si sigue desplegado)
tf_require || error "Terraform no esta listo. Ver infra/README.md."
tf_init lab1 >/dev/null 2>&1 || true
tf_init lab2 >/dev/null 2>&1 || true
S3_BUCKET=$(tf_output lab1 s3_bucket_name)

# ── [1] Eliminar tablas del Glue Catalog ─────────────────────────────────────
echo ""
info "[1/3] Eliminando tablas del Glue Catalog..."

TABLES=$(aws glue get-tables \
  --database-name "financial_platform" \
  --region        "${REGION}" \
  --query         "TableList[*].Name" \
  --output        text 2>/dev/null || echo "")

if [ -n "${TABLES}" ]; then
  for TABLE in ${TABLES}; do
    aws glue delete-table \
      --database-name "financial_platform" \
      --name          "${TABLE}" \
      --region        "${REGION}" 2>/dev/null \
      && success "Tabla eliminada: financial_platform.${TABLE}" \
      || warn "No se pudo eliminar: ${TABLE}"
  done
else
  success "No hay tablas en el Catalog (ya limpias)"
fi

# ── [2] Limpiar zonas curated, rejected, quality-reports en S3 ───────────────
echo ""
info "[2/3] Limpiando datos procesados en S3..."

if [ -n "${S3_BUCKET}" ] && [ "${S3_BUCKET}" != "None" ]; then
  for PREFIX in curated/ rejected/ quality-reports/ scripts/; do
    COUNT=$(aws s3 ls "s3://${S3_BUCKET}/${PREFIX}" \
      --recursive \
      --region "${REGION}" 2>/dev/null | wc -l || echo "0")

    if [ "${COUNT}" -gt 0 ]; then
      aws s3 rm "s3://${S3_BUCKET}/${PREFIX}" \
        --recursive \
        --region "${REGION}" 2>/dev/null || true
      success "Eliminado: s3://${S3_BUCKET}/${PREFIX} (${COUNT} objetos)"
    else
      info "Vacío o no existe: s3://${S3_BUCKET}/${PREFIX}"
    fi
  done
else
  warn "No se encontró el S3 bucket del Lab 1. Limpia manualmente si es necesario."
fi

# ── [3] Destruir la infraestructura del Lab 2 ────────────────────────────────
echo ""
info "[3/3] Ejecutando terraform destroy del Lab 2 (~2 min)..."

if ! tf_deployed lab2; then
  warn "El modulo infra/lab2 no tiene recursos. Nada que eliminar."
else
  TF_STATE_BUCKET=$(grep -E '^\s*bucket\s*=' "${TF_BACKEND_CONFIG}" | head -1 | sed 's/.*= *"\(.*\)"/\1/')
  export TF_VAR_state_bucket="${TF_STATE_BUCKET}"
  export TF_VAR_project_name="${PROJECT_NAME}"
  export TF_VAR_environment="${ENVIRONMENT}"
  export TF_VAR_aws_region="${REGION}"

  tf_destroy lab2 || warn "terraform destroy devolvio error — revisa la consola AWS"

  success "Infraestructura del Lab 2 destruida"
fi

echo ""
echo "============================================================"
echo -e "${GREEN}  ✅ Cleanup Lab 2 completado${NC}"
echo "============================================================"
echo ""
echo "  Para cleanup completo del Lab 1 (bucket + RDS + DMS):"
echo "  → cd ../../01-ingestion-data-lake/scripts && ./cleanup.sh"
echo ""
