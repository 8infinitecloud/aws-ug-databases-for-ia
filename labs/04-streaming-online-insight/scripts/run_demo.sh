#!/usr/bin/env bash
# =============================================================================
# run_demo.sh — publica eventos simulados en IoT Core
# =============================================================================
# Existe por dos razones:
#
#   1. Es el plan B de la charla. Si el POS fisico falla —wifi del local, cable,
#      lo que sea— la demo del Lab 4 sigue funcionando desde el portatil.
#   2. Un ataque de card testing de verdad son veinte operaciones en un minuto,
#      y eso no se teclea en un terminal en directo.
#
# Uso:
#   ./run_demo.sh                 escenario normal (no dispara nada)
#   ./run_demo.sh card-testing    rafaga que dispara DECLINE_RATIO y VELOCITY
#   ./run_demo.sh casa-de-cambio  importe alto en MCC de riesgo
# =============================================================================

set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; BLUE='\033[0;34m'; DIM='\033[2m'; NC='\033[0m'
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

ESCENARIO="${1:-normal}"
SERIAL="${DEVICE_SERIAL:-012370911fd7211101}"
TOPIC="${SERIAL}/events"

ENDPOINT=$(aws iot describe-endpoint --endpoint-type iot:Data-ATS \
    --query endpointAddress --output text)

# Publica un evento con la misma forma que produce payments/backend/app/events.py.
# Si esta forma cambia alli, este script deja de representar la realidad.
publicar() {
    local n="$1" monto="$2" codigo="$3" mcc="$4" riesgo="$5"
    local estado="CAPTURED"; [ "$codigo" != "00" ] && estado="FAILED"

    # Una sola linea, sin saltos. Firehose separa los registros con "\n", asi
    # que un JSON multilinea hace que los limites entre registros dejen de ser
    # distinguibles y Glue no pueda leer el objeto. El backend real ya emite
    # compacto (json.dumps sin indent): esto lo imita.
    local auth="null" motivo="null"
    if [ "$codigo" = "00" ]; then auth='"MOCK-0000"'; else motivo='"Rechazada por el emisor"'; fi

    local payload
    payload=$(printf '%s' \
      "{\"event_type\":\"payment.$([ "$codigo" = "00" ] && echo captured || echo declined)\"," \
      "\"event_version\":\"1.0\",\"transaction_id\":\"ch_DEMO$(printf '%04d' "$n")_1\"," \
      "\"intent_reference\":\"PIDEMO$(printf '%04d' "$n")\",\"merchant_id\":\"MID000000012345\"," \
      "\"mcc\":\"${mcc}\",\"risk_level\":\"${riesgo}\",\"pos_id\":\"TID00001\"," \
      "\"device_id\":\"${SERIAL}\",\"payment_method\":\"CARD_PRESENT\"," \
      "\"amount\":${monto},\"currency\":\"PEN\",\"status\":\"${estado}\"," \
      "\"response_code\":\"${codigo}\",\"auth_code\":${auth}," \
      "\"rrn\":\"$(date +%y%m%d)$(printf '%06d' "$n")\",\"stan\":\"$(printf '%06d' "$n")\"," \
      "\"fee_amount\":0.00,\"net_amount\":${monto},\"decline_reason\":${motivo}," \
      "\"items\":[],\"timestamp\":\"$(date -u +%Y-%m-%dT%H:%M:%S+00:00)\"}")

    aws iot-data publish --endpoint-url "https://${ENDPOINT}" \
        --topic "${TOPIC}" --qos 1 --cli-binary-format raw-in-base64-out \
        --payload "${payload}" >/dev/null

    local marca="${GREEN}OK ${NC}"; [ "$codigo" != "00" ] && marca="${RED}DEC${NC}"
    echo -e "  ${marca} ${monto} PEN  ${DIM}rc=${codigo} mcc=${mcc}${NC}"
}

info "Publicando en ${TOPIC}"
echo

case "${ESCENARIO}" in
  normal)
    info "Una manana normal: ventas espaciadas, todas aprobadas"
    for i in 1 2 3; do publicar "$i" 18.50 00 5812 MEDIUM; sleep 3; done
    ;;
  card-testing)
    info "Card testing: rafaga en el mismo minuto, casi todas rechazadas"
    # Importes de 1.00: quien prueba tarjetas cobra lo minimo para no llamar la
    # atencion, y porque solo quiere saber si la tarjeta vive.
    for i in $(seq 1 14); do
      if [ $((i % 7)) -eq 0 ]; then publicar "$i" 1.00 00 5812 MEDIUM
      else publicar "$i" 1.00 05 5812 MEDIUM; fi
      sleep 1
    done
    ;;
  casa-de-cambio)
    info "Importe alto en un comercio de riesgo alto (MCC 6051)"
    publicar 1 450.00 00 6051 HIGH
    ;;
  *)
    echo "Escenario desconocido: ${ESCENARIO}" >&2
    echo "Usa: normal | card-testing | casa-de-cambio" >&2
    exit 1
    ;;
esac

echo
info "Mira el scoring:"
echo "  aws logs tail \$(terraform -chdir=infra/lab4 output -raw scorer_log_group) --follow"
echo
info "Los eventos tardan ~60 s en aparecer en el lago (buffer de Firehose)."
