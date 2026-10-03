#!/usr/bin/env bash
# =============================================================================
# cleanup.sh — Lab 4
# =============================================================================
# El Lab 4 se destruye PRIMERO de todos: su Firehose escribe en el bucket del
# Lab 1, y su regla de IoT cuelga del endpoint de la cuenta.
#
# Nada de este lab tiene retencion ni proteccion de borrado:
#   - DynamoDB sin deletion_protection y sin PITR
#   - Firehose sin destino que sobreviva
#   - Los objetos que dejo en S3 pertenecen al bucket del Lab 1, que tiene
#     force_destroy
#
# Uso:
#   ./cleanup.sh          pide confirmacion
#   ./cleanup.sh --force  sin preguntar
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo
info "Cleanup del Lab 4"

if ! tf_deployed lab4; then
    success "El Lab 4 no esta desplegado: nada que borrar."
    exit 0
fi

echo
warn "Se van a destruir las tablas de velocidad y de estado, la Lambda, Firehose y las reglas de IoT."
warn "La telemetria ya escrita en s3://.../raw/telemetry/ NO se borra aqui:"
warn "pertenecen al bucket del Lab 1 y se van con su cleanup."
echo

if [ "${1:-}" != "--force" ]; then
    read -r -p "  Continuar? (escribe 'si'): " RESP
    [ "${RESP}" = "si" ] || { info "Cancelado."; exit 0; }
fi

tf_destroy lab4 || { echo -e "${RED}[✗]${NC} terraform destroy fallo."; exit 1; }

echo
success "Lab 4 destruido"
info "Comprueba que no queda nada:"
echo "  terraform -chdir=infra/lab4 state list"
echo
