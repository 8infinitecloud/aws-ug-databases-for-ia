#!/usr/bin/env bash
# =============================================================================
# tf.sh — helpers compartidos para hablar con Terraform desde los scripts
# =============================================================================
# Se sourcea, no se ejecuta:
#
#   source "${REPO_ROOT}/scripts/lib/tf.sh"
#   BUCKET=$(tf_output lab1 s3_bucket_name)
#
# Sustituye a los 'aws cloudformation describe-stacks --query Outputs[...]'
# que usaban los scripts cuando la infraestructura era CloudFormation.
# =============================================================================

# Raiz del repositorio, independientemente de desde donde se invoque el script.
TF_REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TF_INFRA_DIR="${TF_REPO_ROOT}/infra"
TF_BACKEND_CONFIG="${TF_INFRA_DIR}/backend.hcl"

# Comprueba que terraform existe y que el backend esta bootstrapeado.
tf_require() {
  command -v terraform >/dev/null 2>&1 || {
    echo "[✗] terraform no encontrado. Instala: https://developer.hashicorp.com/terraform/downloads" >&2
    return 1
  }
  if [ ! -f "${TF_BACKEND_CONFIG}" ]; then
    echo "[✗] Falta ${TF_BACKEND_CONFIG}." >&2
    echo "    Ejecuta primero el bootstrap del backend remoto:" >&2
    echo "      cd infra/bootstrap && terraform init && terraform apply" >&2
    return 1
  fi
}

# tf_init <modulo>  — idempotente; -reconfigure evita preguntas interactivas.
tf_init() {
  terraform -chdir="${TF_INFRA_DIR}/$1" init \
    -backend-config="${TF_BACKEND_CONFIG}" \
    -input=false \
    -reconfigure \
    >/dev/null
}

# tf_apply <modulo> [args...]
tf_apply() {
  local module="$1"; shift
  terraform -chdir="${TF_INFRA_DIR}/${module}" apply -input=false -auto-approve "$@"
}

# tf_destroy <modulo> [args...]
tf_destroy() {
  local module="$1"; shift
  terraform -chdir="${TF_INFRA_DIR}/${module}" destroy -input=false -auto-approve "$@"
}

# tf_output <modulo> <nombre>  — cadena vacia si no existe, para que el
# 'set -euo pipefail' de los scripts no aborte al comprobar si algo esta creado.
tf_output() {
  terraform -chdir="${TF_INFRA_DIR}/$1" output -raw "$2" 2>/dev/null || echo ""
}

# tf_deployed <modulo>  — 0 si el modulo tiene recursos en su state.
tf_deployed() {
  local n
  n=$(terraform -chdir="${TF_INFRA_DIR}/$1" state list 2>/dev/null | wc -l | tr -d ' ')
  [ "${n:-0}" -gt 0 ]
}
