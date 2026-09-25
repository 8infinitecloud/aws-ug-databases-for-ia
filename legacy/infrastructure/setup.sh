#!/bin/bash
# setup.sh — Provisiona la infraestructura AWS del laboratorio RAG.
#
# Solo toca recursos de AWS (CloudFormation, VPC, Aurora, Redis, DynamoDB, S3, EC2).
# El despliegue de la aplicación es responsabilidad de deploy-app.sh.
#
# Tiempo estimado: 20-25 minutos (dominado por Aurora Serverless v2 + EC2 boot).
# Ejecutar ANTES del demo; deploy-app.sh se puede correr cuantas veces sea necesario.

set -euo pipefail

STACK_NAME="financial-compliance-rag-lab"
REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="rag-lab"
DB_PASSWORD="${DB_PASSWORD:-RagLab2024!}"
ENVIRONMENT="${ENVIRONMENT:-lab}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "============================================================"
echo "  FinCorp RAG Lab — Provisioning de Infraestructura"
echo "============================================================"
echo "  Stack:  $STACK_NAME"
echo "  Región: $REGION"
echo "  Env:    $ENVIRONMENT"
[ -n "${AWS_PROFILE:-}" ] && echo "  Perfil: $AWS_PROFILE"
echo "============================================================"

# ─────────────────────────────────────────────────────────────────────────────
# [1/3] Prerequisitos
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[1/3] Verificando prerequisitos..."

command -v aws >/dev/null 2>&1 || { echo "❌ AWS CLI no encontrado."; exit 1; }

AWS_ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
echo "  ✓ AWS Account: $AWS_ACCOUNT | Región: $REGION"

TITAN_AVAILABLE=$(aws bedrock list-foundation-models \
  --region "$REGION" \
  --query "modelSummaries[?modelId=='amazon.titan-embed-text-v2:0'].modelId" \
  --output text 2>/dev/null || echo "")
if [ -z "$TITAN_AVAILABLE" ]; then
  echo "  ⚠️  Titan Embeddings V2 sin acceso. Ve a: Bedrock Console → Model access"
fi

# ─────────────────────────────────────────────────────────────────────────────
# [2/3] Deploy CloudFormation
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[2/3] Desplegando CloudFormation stack (~15-20 min)..."
echo "  ⏱  Aurora Serverless v2 es el recurso más lento. Toma un café ☕"

aws cloudformation deploy \
  --stack-name "$STACK_NAME" \
  --template-file "$SCRIPT_DIR/cloudformation.yaml" \
  --parameter-overrides \
    ProjectName="$PROJECT_NAME" \
    DBPassword="$DB_PASSWORD" \
    EnvironmentType="$ENVIRONMENT" \
  --capabilities CAPABILITY_NAMED_IAM \
  --region "$REGION" \
  --no-fail-on-empty-changeset

echo "  ✓ Stack desplegado"

# ─────────────────────────────────────────────────────────────────────────────
# [3/3] Mostrar outputs
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[3/3] Outputs del stack..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text
}

echo "  Aurora:      $(get_output AuroraEndpoint)"
echo "  Redis:       $(get_output RedisEndpoint)"
echo "  S3:          $(get_output S3BucketName)"
echo "  EC2 ID:      $(get_output AppServerInstanceId)"
echo "  Streamlit:   $(get_output StreamlitURL)"

echo ""
echo "============================================================"
echo "  ✅ Infraestructura lista"
echo "============================================================"
echo ""
echo "  Próximo paso — desplegar la aplicación:"
echo "     ./deploy-app.sh"
echo ""
echo "  Al terminar el lab:"
echo "     ./cleanup.sh"
echo ""
