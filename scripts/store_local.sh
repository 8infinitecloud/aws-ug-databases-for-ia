#!/usr/bin/env bash
# =============================================================================
# store_local.sh — arranca la tienda Mercado Andino en local, en Docker
# =============================================================================
# La tienda es el COMERCIO: un sistema aparte que cobra a traves de la API de
# FinCorp con su propia clave. Este script la conecta con el core desplegado:
#
#   FINCORP_API_URL   ← terraform -chdir=infra/payments output api_endpoint
#   STORE_API_KEY     ← .demo_keys.json, la clave de Mercado Andino
#                       (la crea: python scripts/provision_demo_keys.py --ssm)
#
# Uso:
#   ./scripts/store_local.sh            # http://localhost:8090
#   PUERTO=9000 ./scripts/store_local.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
PUERTO="${PUERTO:-8090}"
MID="MID000000020012"   # Mercado Andino, en payments/schema/005_demo_portfolio.sql

error() { echo -e "\033[0;31m[✗]\033[0m $*" >&2; exit 1; }

command -v docker >/dev/null || error "Falta docker."
docker info >/dev/null 2>&1 || error "El daemon de docker no responde. ¿Docker Desktop esta abierto?"

tf_init payments >/dev/null 2>&1 || true
API=$(tf_output payments api_endpoint)
[ -n "${API}" ] || error "El core no esta desplegado: ./scripts/deploy_core.sh"

CLAVES="${REPO_ROOT}/.demo_keys.json"
[ -f "${CLAVES}" ] || error "Faltan las claves: python scripts/provision_demo_keys.py --ssm"
CLAVE=$(jq -r --arg m "${MID}" '.[$m] // empty' "${CLAVES}")
[ -n "${CLAVE}" ] || error "No hay clave para Mercado Andino en ${CLAVES}. Vuelve a ejecutar provision_demo_keys.py --ssm"

docker build -q -t mercado-andino "${REPO_ROOT}/store" >/dev/null
docker rm -f mercado-andino >/dev/null 2>&1 || true
# La clave va por entorno al contenedor, no en la imagen ni en un fichero.
docker run -d --name mercado-andino -p "${PUERTO}:8090" \
  -e FINCORP_API_URL="${API}" \
  -e STORE_API_KEY="${CLAVE}" \
  -e STORE_TERMINAL_ID="TID00010" \
  -e STORE_PUBLIC_URL="http://localhost:${PUERTO}" \
  mercado-andino >/dev/null

echo -e "\033[0;32m[✓]\033[0m Mercado Andino en http://localhost:${PUERTO}"
echo "    cobra a traves de ${API}"
echo "    parar: docker rm -f mercado-andino"
