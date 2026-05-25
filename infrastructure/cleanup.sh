#!/bin/bash
# cleanup.sh — Elimina todos los recursos AWS del laboratorio para evitar costos.
#
# EJECUTAR AL TERMINAR EL LAB. Aurora y ElastiCache cuestan ~$15-25 USD/día si se dejan corriendo.

set -euo pipefail

STACK_NAME="financial-compliance-rag-lab"
REGION="${AWS_REGION:-us-east-1}"

echo "============================================================"
echo "  ⚠️  CLEANUP — Eliminando recursos del lab RAG"
echo "============================================================"
echo ""
echo "  Esto eliminará PERMANENTEMENTE:"
echo "  - Stack CloudFormation (VPC, Aurora, ElastiCache, EC2 App Server)"
echo "  - Bucket S3 (vaciado y eliminado)"
echo "  - Tabla DynamoDB"
echo "  - Secrets Manager secrets"
echo ""
read -p "  ¿Continuar? (escribe 'si' para confirmar): " CONFIRM

if [ "$CONFIRM" != "si" ]; then
  echo "Cleanup cancelado."
  exit 0
fi

echo ""
echo "[1/3] Vaciando bucket S3..."
S3_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name "$STACK_NAME" \
  --region "$REGION" \
  --query "Stacks[0].Outputs[?OutputKey=='S3BucketName'].OutputValue" \
  --output text 2>/dev/null || echo "")

if [ -n "$S3_BUCKET" ]; then
  aws s3 rm "s3://$S3_BUCKET" --recursive --region "$REGION" 2>/dev/null || true
  echo "  ✓ Bucket vaciado: $S3_BUCKET"
fi

echo ""
echo "[2/3] Eliminando tabla DynamoDB..."
aws dynamodb delete-table \
  --table-name rag_user_memory \
  --region "$REGION" 2>/dev/null || echo "  (tabla no existía o ya eliminada)"
echo "  ✓ Tabla DynamoDB eliminada"

echo ""
echo "[3/3] Eliminando CloudFormation stack (10-15 min)..."
aws cloudformation delete-stack \
  --stack-name "$STACK_NAME" \
  --region "$REGION"

echo "  Esperando que el stack se elimine..."
aws cloudformation wait stack-delete-complete \
  --stack-name "$STACK_NAME" \
  --region "$REGION"

echo "  ✓ Stack eliminado"

# Limpiar el .env local
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
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
echo "  - CloudFormation: https://console.aws.amazon.com/cloudformation/home"
echo ""
