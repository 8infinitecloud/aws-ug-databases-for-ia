#!/usr/bin/env bash
# =============================================================================
# validate.sh — Lab 4: comprueba que el scoring online funciona de verdad
# =============================================================================
# No comprueba que los recursos existan —eso ya lo dice terraform— sino que el
# camino completo funciona: publicar un evento, que la Lambda lo puntue y que la
# alerta quede en DynamoDB.
#
# Uso:
#   ./validate.sh
# =============================================================================

set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC} $*"; }
success() { echo -e "${GREEN}[✓]${NC} $*"; }
warn()    { echo -e "${YELLOW}[⚠]${NC} $*"; }
fail()    { echo -e "${RED}[✗]${NC} $*"; FALLOS=$((FALLOS + 1)); }

FALLOS=0
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

# shellcheck source=../../../scripts/lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

echo
info "Validando el Lab 4"
echo

tf_deployed lab4 || { echo -e "${RED}[✗]${NC} El Lab 4 no esta desplegado."; exit 1; }

TABLA=$(tf_output lab4 velocity_table_name)
LAMBDA=$(tf_output lab4 scorer_function_name)
STREAM=$(tf_output lab4 firehose_stream_name)
ESTADO=$(tf_output lab4 terminal_state_table)

# ── 1. Los recursos responden ────────────────────────────────────────────────
if aws dynamodb describe-table --table-name "${TABLA}" \
     --query 'Table.TableStatus' --output text 2>/dev/null | grep -q ACTIVE; then
    success "Tabla ${TABLA} activa"
else
    fail "La tabla ${TABLA} no esta activa"
fi

if aws lambda get-function --function-name "${LAMBDA}" >/dev/null 2>&1; then
    CONC=$(aws lambda get-function-concurrency --function-name "${LAMBDA}" \
             --query ReservedConcurrentExecutions --output text 2>/dev/null)
    success "Lambda ${LAMBDA} desplegada (concurrencia reservada: ${CONC})"
else
    fail "No se encuentra la Lambda ${LAMBDA}"
fi

if aws firehose describe-delivery-stream --delivery-stream-name "${STREAM}" \
     --query 'DeliveryStreamDescription.DeliveryStreamStatus' --output text 2>/dev/null \
     | grep -q ACTIVE; then
    success "Firehose ${STREAM} activo"
else
    fail "Firehose ${STREAM} no esta activo"
fi

# ── 2. El scoring de verdad ──────────────────────────────────────────────────
# Se invoca la Lambda directamente en vez de publicar por MQTT: asi se prueba el
# scoring sin depender de que la regla de IoT enrute, que se valida aparte.
info "Invocando la Lambda con un evento que DEBE marcarse..."

EVENTO=$(cat <<'JSON'
{"event_type":"payment.captured","event_version":"1.0",
 "transaction_id":"ch_VALIDATE_1","merchant_id":"MID000000012345",
 "mcc":"6051","risk_level":"HIGH","pos_id":"TID_VALIDATE",
 "payment_method":"CARD_PRESENT","amount":450.0,"currency":"PEN",
 "status":"CAPTURED","response_code":"00"}
JSON
)

RESP=$(aws lambda invoke --function-name "${LAMBDA}" \
         --cli-binary-format raw-in-base64-out --payload "${EVENTO}" \
         /tmp/lab4-validate.json --query StatusCode --output text 2>/dev/null || echo "ERROR")

if [ "${RESP}" = "200" ] && grep -q '"flagged": *true' /tmp/lab4-validate.json 2>/dev/null; then
    success "La Lambda marco la operacion: $(cat /tmp/lab4-validate.json)"
else
    fail "La Lambda no marco una operacion que deberia marcar: $(cat /tmp/lab4-validate.json 2>/dev/null)"
fi

# ── 3. La alerta quedo guardada ──────────────────────────────────────────────
sleep 2
ALERTAS=$(aws dynamodb query --table-name "${TABLA}" \
    --key-condition-expression "pk = :t AND begins_with(sk, :a)" \
    --expression-attribute-values '{":t":{"S":"T#TID_VALIDATE"},":a":{"S":"ALERT#"}}' \
    --query 'Count' --output text 2>/dev/null || echo 0)

if [ "${ALERTAS}" -gt 0 ]; then
    success "La alerta quedo en DynamoDB (${ALERTAS} encontrada/s)"
else
    fail "No hay alerta en DynamoDB: el scoring marco pero no persistio"
fi

# ── 4. Un evento normal NO se marca ──────────────────────────────────────────
# Una regla que marca todo no protege de nada: hay que comprobar los dos lados.
info "Invocando con un evento corriente, que NO debe marcarse..."
NORMAL='{"event_type":"payment.captured","event_version":"1.0","transaction_id":"ch_VALIDATE_2","merchant_id":"MID000000012345","mcc":"5812","risk_level":"MEDIUM","pos_id":"TID_NORMAL","payment_method":"CARD_PRESENT","amount":18.5,"currency":"PEN","status":"CAPTURED","response_code":"00"}'

aws lambda invoke --function-name "${LAMBDA}" --cli-binary-format raw-in-base64-out \
    --payload "${NORMAL}" /tmp/lab4-normal.json >/dev/null 2>&1 || true

if grep -q '"flagged": *false' /tmp/lab4-normal.json 2>/dev/null; then
    success "Una venta corriente no se marca"
else
    fail "Se marco una venta corriente: los umbrales estan mal calibrados"
fi

# ── 5. La telemetria llega al ultimo estado del terminal ─────────────────────
# Esta si pasa por IoT Core: lo que se prueba es la regla, que es quien escribe
# en DynamoDB. Un serial de validacion, para no pisar el estado de un terminal
# real.
info "Publicando un latido de telemetria de prueba..."
ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS \
             --query endpointAddress --output text)
aws iot-data publish --endpoint-url "https://${ENDPOINT}" \
    --topic "validate-serial/telemetry" --qos 1 --cli-binary-format raw-in-base64-out \
    --payload '{"type":"heartbeat","rssi_dbm":-61,"battery_pct":88}' >/dev/null

LATIDO=""
for _ in 1 2 3 4 5 6; do
    sleep 2
    LATIDO=$(aws dynamodb get-item --table-name "${ESTADO}" \
        --key '{"device_id":{"S":"validate-serial"}}' \
        --query 'Item.rssi_dbm.N' --output text 2>/dev/null || true)
    [ -n "${LATIDO}" ] && [ "${LATIDO}" != "None" ] && break
done
if [ "${LATIDO}" = "-61" ]; then
    success "El latido llego a ${ESTADO} por la regla de IoT"
else
    fail "El latido no aparecio en ${ESTADO}: revisa el log de errores de la regla"
fi
aws dynamodb delete-item --table-name "${ESTADO}" \
    --key '{"device_id":{"S":"validate-serial"}}' >/dev/null 2>&1 || true

# ── Resumen ──────────────────────────────────────────────────────────────────
echo
if [ "${FALLOS}" -eq 0 ]; then
    success "Lab 4 validado"
    echo
    info "Un ataque por la API real:  python scripts/generate_traffic.py --api \$API --card-testing"
    info "Sin API:                    ./run_demo.sh card-testing"
else
    echo -e "${RED}[✗]${NC} ${FALLOS} comprobacion(es) fallaron"
    exit 1
fi
echo
