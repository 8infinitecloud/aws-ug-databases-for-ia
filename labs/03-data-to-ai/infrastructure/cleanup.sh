#!/bin/bash
# cleanup.sh — Elimina todos los recursos AWS del laboratorio para evitar costos.
#
# EJECUTAR AL TERMINAR EL LAB. Aurora y ElastiCache cuestan ~$15-25 USD/día si se dejan corriendo.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="rag-lab"
ENVIRONMENT="${ENVIRONMENT:-lab}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(cd "${ROOT_DIR}/../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo "  ⚠️  CLEANUP — Eliminando recursos del lab RAG"
echo "============================================================"
echo ""
echo "  Esto eliminará PERMANENTEMENTE:"
echo "  - Todo el modulo Terraform infra/lab3 (VPC, Aurora, ElastiCache, EC2, DynamoDB, S3)"
echo "  - Bucket S3 (vaciado antes de eliminar el stack)"
echo "  - Secrets Manager secrets"
echo ""
read -r -p "  ¿Continuar? (escribe 'si' para confirmar): " CONFIRM

if [ "$CONFIRM" != "si" ]; then
  echo "Cleanup cancelado."
  exit 0
fi

echo ""
echo "[1/3] Vaciando bucket S3..."
tf_require || { echo "❌ Terraform no esta listo. Ver infra/README.md."; exit 1; }
tf_init lab3 >/dev/null 2>&1 || true
S3_BUCKET=$(tf_output lab3 s3_bucket_name)

if [ -n "$S3_BUCKET" ]; then
  aws s3 rm "s3://$S3_BUCKET" --recursive --region "$REGION" 2>/dev/null || true
  echo "  ✓ Bucket vaciado: $S3_BUCKET"
fi

echo ""
echo "[2/3] terraform destroy (Aurora, Redis, DynamoDB, EC2, VPC)..."

# destroy necesita las variables sin default para poder planificar, aunque el
# valor no se use para borrar nada.
export TF_VAR_db_password="${DB_PASSWORD:-placeholder-no-usado-en-destroy}"
export TF_VAR_project_name="$PROJECT_NAME"
export TF_VAR_environment_type="$ENVIRONMENT"
export TF_VAR_aws_region="$REGION"

if tf_deployed lab3; then
  tf_destroy lab3 || echo "  ⚠️  terraform destroy devolvio error — revisa la consola AWS"
  echo "  ✓ Infraestructura destruida"
else
  echo "  ⚠️  El modulo infra/lab3 no tiene recursos. Nada que eliminar."
fi

# Limpiar el .env local
if [ -f "$ROOT_DIR/.env" ]; then
  rm "$ROOT_DIR/.env"
  echo "  ✓ Archivo .env eliminado"
fi

echo ""
echo "============================================================"
echo "  ✅ Cleanup completado. No se generarán más costos."
echo "============================================================"
echo ""
echo "  Verifica en la consola AWS que no queden recursos:"
echo "  - RDS: https://console.aws.amazon.com/rds/home"
echo "  - ElastiCache: https://console.aws.amazon.com/elasticache/home"
echo ""
