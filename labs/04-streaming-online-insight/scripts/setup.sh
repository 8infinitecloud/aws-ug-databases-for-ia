#!/usr/bin/env bash
# =============================================================================
# setup.sh — Lab 4: Streaming e insight online
# =============================================================================
# Prerrequisito: el Lab 1 desplegado (Firehose aterriza los eventos en su bucket).
#
# Acciones:
#   1. Comprueba que el Lab 1 existe y tiene bucket
#   2. Aplica el modulo Terraform infra/lab4
#   3. Muestra los outputs y como ver el scoring en vivo
#   NO publica eventos — eso lo hace run_demo.sh
#
# Uso:
#   ./setup.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }
error()   { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo
info "Lab 4 — Streaming e insight online"
echo

tf_require || error "Terraform no esta listo. Ver infra/README.md."

# ── El Lab 1 tiene que existir: Firehose escribe en su bucket ────────────────
tf_init lab1 >/dev/null 2>&1 || true
if ! tf_deployed lab1; then
    error "El Lab 1 no esta desplegado. Firehose necesita su bucket.
       Ejecuta antes: labs/01-ingestion-data-lake/scripts/setup.sh"
fi
S3_BUCKET=$(tf_output lab1 s3_bucket_name)
success "Lab 1 encontrado — bucket ${S3_BUCKET}"

# ── Desplegar ────────────────────────────────────────────────────────────────
info "Aplicando infra/lab4..."
tf_init lab4 || error "terraform init fallo. Revisa infra/README.md."
tf_apply lab4 || error "terraform apply fallo."

TABLA=$(tf_output lab4 velocity_table_name)
LAMBDA=$(tf_output lab4 scorer_function_name)
LOGS=$(tf_output lab4 scorer_log_group)
TOPIC=$(tf_output lab4 iot_topic_filter)
TELEMETRIA=$(tf_output lab4 telemetry_s3_path)
ESTADO=$(tf_output lab4 terminal_state_table)

echo
success "Lab 4 desplegado"
echo
echo "  Tabla de velocidad : ${TABLA}"
echo "  Lambda del scoring : ${LAMBDA}"
echo "  Topic de pagos     : ${TOPIC}"
echo "  Telemetria al lago : ${TELEMETRIA}"
echo "  Estado de la flota : ${ESTADO}"
echo
info "Ver el scoring en vivo (dejalo en una terminal aparte):"
echo "  aws logs tail ${LOGS} --follow"
echo
info "Generar trafico sin el POS fisico:"
echo "  ./run_demo.sh card-testing"
echo
