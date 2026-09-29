#!/bin/bash
# setup.sh — Provisiona la infraestructura AWS del laboratorio RAG.
#
# Solo toca recursos de AWS (CloudFormation, VPC, Aurora, Redis, DynamoDB, S3, EC2).
# El despliegue de la aplicación es responsabilidad de deploy-app.sh.
#
# Tiempo estimado: 20-25 minutos (dominado por Aurora Serverless v2 + EC2 boot).
# Ejecutar ANTES del demo; deploy-app.sh se puede correr cuantas veces sea necesario.

set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
PROJECT_NAME="rag-lab"
# Precedencia: TF_VAR_db_password (lo que pide la guia) > DB_PASSWORD.
# Antes se exportaba TF_VAR_db_password desde DB_PASSWORD sin mirar si el usuario
# ya la habia definido, pisandola silenciosamente con el valor por defecto.
#
# Y SIN valor por defecto, que es lo importante. Antes caia en uno cableado en
# este mismo fichero: quien seguia el taller y olvidaba exportar la variable
# desplegaba un Aurora con una contrasena publicada en el repositorio. Ahora
# falla y lo dice, que es lo unico razonable cuando lo que falta es un secreto.
DB_PASSWORD="${TF_VAR_db_password:-${DB_PASSWORD:-}}"
if [ -z "${DB_PASSWORD}" ]; then
    echo "[✗] Falta la contrasena de la base de datos." >&2
    echo "    Defínela antes de continuar (minimo 8 caracteres):" >&2
    echo "      export TF_VAR_db_password='TuContrasenaSegura2026!'" >&2
    exit 1
fi
ENVIRONMENT="${ENVIRONMENT:-lab}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo "============================================================"
echo "  FinCorp RAG Lab — Provisioning de Infraestructura"
echo "============================================================"
echo "  Modulo: infra/lab3"
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
tf_require || { echo "❌ Terraform no esta listo. Ver infra/README.md."; exit 1; }

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
echo "[2/3] Aplicando infraestructura con Terraform (~15-20 min)..."
echo "  ⏱  Aurora Serverless v2 es el recurso más lento. Toma un café ☕"

tf_init lab3 || { echo "❌ terraform init fallo."; exit 1; }

# La contraseña viaja por TF_VAR_, no como argumento: -var la dejaria en el
# historial del shell y en la lista de procesos.
export TF_VAR_db_password="$DB_PASSWORD"
export TF_VAR_project_name="$PROJECT_NAME"
export TF_VAR_environment_type="$ENVIRONMENT"
export TF_VAR_aws_region="$REGION"

tf_apply lab3 || { echo "❌ terraform apply fallo."; exit 1; }

echo "  ✓ Infraestructura aplicada"

# ─────────────────────────────────────────────────────────────────────────────
# [3/3] Mostrar outputs
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[3/3] Outputs de Terraform..."

echo "  Aurora:      $(tf_output lab3 aurora_endpoint)"
echo "  Redis:       $(tf_output lab3 redis_endpoint)"
echo "  S3:          $(tf_output lab3 s3_bucket_name)"
echo "  EC2 ID:      $(tf_output lab3 app_server_instance_id)"
echo "  Streamlit:   $(tf_output lab3 streamlit_url)"

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
