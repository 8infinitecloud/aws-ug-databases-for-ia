#!/usr/bin/env bash
# =============================================================================
# validate.sh — Lab 1: la replicacion funciona de punta a punta
# =============================================================================
# Comprueba, en este orden:
#   1. La tarea DMS esta en `running` con la foto inicial al 100%
#   2. Cada tabla replicada termino su foto inicial (estadisticas de DMS)
#   3. EL CDC FUNCIONA: hace un pago de verdad por la API y espera a que ese
#      cambio aparezca en raw/ como un fichero nuevo. Es la prueba que importa:
#      que la foto inicial exista solo dice que DMS sabe leer, no que escucha.
#   4. El slot de replicacion del core esta activo (alguien consume su WAL)
#   5. La cartera legacy esta en raw/legacy/
#
# Uso: ./validate.sh            (con el pago de prueba)
#      ./validate.sh --sin-pago (sin tocar el core)
# =============================================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
pass() { echo -e "${GREEN}[PASS]${NC} $*"; }
fail() { echo -e "${RED}[FAIL]${NC} $*"; FAILURES=$((FAILURES + 1)); }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
FAILURES=0

REGION="${AWS_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
# shellcheck source=../../../scripts/lib/core.sh
source "${REPO_ROOT}/scripts/lib/core.sh"

echo "============================================================"
echo "  Lab 1 — Validacion de la replicacion (full load + CDC)"
echo "============================================================"

tf_init lab1 >/dev/null 2>&1 || true
tf_deployed lab1 || { fail "infra/lab1 sin recursos. Ejecuta setup.sh primero."; exit 1; }

S3_BUCKET=$(tf_output lab1 s3_bucket_name)
DMS_TASK_ARN=$(tf_output lab1 dms_task_arn)
TABLAS=$(terraform -chdir="${TF_INFRA_DIR}/lab1" output -json replicated_tables | jq -r '.[]')

# ── [1] La tarea ─────────────────────────────────────────────────────────────
echo ""
info "[1/5] Tarea DMS..."
read -r ESTADO PCT < <(aws dms describe-replication-tasks \
  --filters "Name=replication-task-arn,Values=${DMS_TASK_ARN}" --region "${REGION}" \
  --query "ReplicationTasks[0].[Status, ReplicationTaskStats.FullLoadProgressPercent]" \
  --output text 2>/dev/null || echo "desconocido 0")
if [ "${ESTADO}" = "running" ] && [ "${PCT}" = "100" ]; then
  pass "running, foto inicial al 100% y CDC escuchando"
else
  fail "estado ${ESTADO}, foto inicial al ${PCT}%. Arranca la tarea con ./setup.sh"
fi

# ── [2] La foto inicial de cada tabla ────────────────────────────────────────
# Por las estadisticas de la tarea y no buscando ficheros: DMS no escribe nada
# para una tabla vacia (refunds, disputes y payouts lo estan hasta que algun
# flujo las use), y buscar LOAD*.parquet las daba por fallidas.
echo ""
info "[2/5] Foto inicial de cada tabla (estadisticas de DMS)..."
STATS=$(aws dms describe-table-statistics --replication-task-arn "${DMS_TASK_ARN}" \
  --region "${REGION}" --output json 2>/dev/null || echo '{"TableStatistics":[]}')
for t in ${TABLAS}; do
  LINEA=$(echo "${STATS}" | jq -r --arg t "${t}" '.TableStatistics[] | select(.TableName == $t)
    | "\(.TableState)|\(.FullLoadRows)|\(.Inserts)|\(.Updates)|\(.Deletes)"')
  IFS='|' read -r ESTADO_T FILAS INS UPD DEL <<< "${LINEA:-ausente|0|0|0|0}"
  if [ "${ESTADO_T}" = "Table completed" ]; then
    pass "${t}: ${FILAS} filas en la foto inicial · CDC: ${INS} I / ${UPD} U / ${DEL} D"
  else
    fail "${t}: estado ${ESTADO_T}"
  fi
done

# ── [3] El CDC, con un pago de verdad ────────────────────────────────────────
echo ""
info "[3/5] CDC: un pago real y su delta en raw/..."
contar_deltas() {
  # Los ficheros de cambios van en carpetas por fecha; la foto inicial, no.
  aws s3 ls "s3://${S3_BUCKET}/raw/acquiring/payment_intents/" --recursive --region "${REGION}" \
    2>/dev/null | grep -v "LOAD" | grep -c "\.parquet" || true
}
if [ "${1:-}" = "--sin-pago" ]; then
  info "     omitido (--sin-pago)"
else
  API=$(tf_output payments api_endpoint)
  ANTES=$(contar_deltas)
  REF="PIVAL$(date +%s)"
  # Un QR de 1 sol en un terminal virtual: no toca el Core2 ni sus contadores.
  curl -fsS -X POST "${API}/v1/intents" -H "Content-Type: application/json" \
    -H "Idempotency-Key: val-c-${REF}" \
    -d "{\"intent_reference\":\"${REF}\",\"terminal_id\":\"TID00002\",\"amount_minor\":100}" >/dev/null \
  && curl -fsS -X POST "${API}/v1/intents/${REF}/confirm" -H "Content-Type: application/json" \
    -H "Idempotency-Key: val-q-${REF}" -d '{"channel":"QR"}' >/dev/null \
  || fail "no se pudo hacer el pago de prueba contra ${API}"

  # DMS escribe un fichero de cambios cada ~30 s (cdc_max_batch_interval).
  DESPUES=${ANTES}
  for _ in $(seq 1 12); do
    sleep 10
    DESPUES=$(contar_deltas)
    [ "${DESPUES}" -gt "${ANTES}" ] && break
    echo -n "."
  done
  echo ""
  if [ "${DESPUES}" -gt "${ANTES}" ]; then
    pass "el pago ${REF} llego a raw/ como delta (${ANTES} -> ${DESPUES} ficheros de cambios)"
  else
    fail "en 2 minutos no aparecio ningun delta: revisa el log de la tarea DMS"
  fi
fi

# ── [4] El slot del core ─────────────────────────────────────────────────────
echo ""
info "[4/5] Slot de replicacion en el core..."
SLOTS=$(core_sql "SELECT slot_name || ' activo=' || active || ' retenido=' || pg_size_pretty(pg_wal_lsn_diff(pg_current_wal_lsn(), restart_lsn)) FROM pg_replication_slots" || true)
if echo "${SLOTS}" | grep -q "activo=t"; then
  pass "$(echo "${SLOTS}" | head -1)"
else
  fail "ningun slot activo: ${SLOTS:-ninguno}. Un slot inactivo retiene WAL sin que nadie lo lea."
fi

# ── [5] La cartera legacy ────────────────────────────────────────────────────
echo ""
info "[5/5] Cartera legacy..."
if aws s3 ls "s3://${S3_BUCKET}/raw/legacy/transactions/transactions.csv" --region "${REGION}" >/dev/null 2>&1; then
  pass "raw/legacy/transactions/transactions.csv"
else
  fail "falta raw/legacy/transactions/transactions.csv: vuelve a ejecutar ./setup.sh"
fi

echo ""
echo "============================================================"
if [ "${FAILURES}" -eq 0 ]; then
  echo -e "${GREEN}  ✅ Lab 1 validado${NC}  →  siguiente: labs/02-transformation-quality-catalog"
else
  echo -e "${RED}  ❌ ${FAILURES} comprobacion(es) fallaron${NC}"
fi
echo "============================================================"
exit ${FAILURES}
