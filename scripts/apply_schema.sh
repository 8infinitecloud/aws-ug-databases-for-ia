#!/usr/bin/env bash
# =============================================================================
# apply_schema.sh — reaplica payments/schema/ sobre la base viva del core
# =============================================================================
# La instancia aplica el esquema sola en su primer arranque. Esto es para
# DESPUES: cambiaste un .sql, hiciste `terraform apply` en infra/payments (que
# sube los ficheros nuevos al bucket), y quieres que la base los tenga.
#
# No abre puertos ni tuneles: le pide a la instancia, por SSM Run Command, que
# ejecute su propio /usr/local/bin/fincorp-apply-schema. Todos los .sql son
# idempotentes, asi que reaplicar es seguro.
#
# Uso:
#   ./scripts/apply_schema.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"

tf_init payments >/dev/null 2>&1 || true
tf_deployed payments || { echo "[✗] infra/payments no esta desplegado." >&2; exit 1; }
INSTANCIA=$(tf_output payments db_instance_id)
REGION="${AWS_REGION:-us-east-1}"

echo "[INFO] Reaplicando el esquema en ${INSTANCIA}…"
CMD=$(aws ssm send-command --region "${REGION}" \
        --instance-ids "${INSTANCIA}" \
        --document-name AWS-RunShellScript \
        --comment "FinCorp: reaplicar payments/schema" \
        --parameters 'commands=["/usr/local/bin/fincorp-apply-schema"]' \
        --query Command.CommandId --output text)

# get-command-invocation falla con InvocationDoesNotExist durante los primeros
# segundos: el comando existe pero la instancia aun no lo ha recogido.
for _ in $(seq 1 30); do
    sleep 2
    ESTADO=$(aws ssm get-command-invocation --region "${REGION}" \
               --command-id "${CMD}" --instance-id "${INSTANCIA}" \
               --query Status --output text 2>/dev/null || echo Pending)
    case "${ESTADO}" in
        Success|Failed|Cancelled|TimedOut) break ;;
    esac
done

aws ssm get-command-invocation --region "${REGION}" --command-id "${CMD}" \
    --instance-id "${INSTANCIA}" --query '[StandardOutputContent,StandardErrorContent]' \
    --output text

if [ "${ESTADO}" = "Success" ]; then
    echo "[✓] Esquema reaplicado"
else
    echo "[✗] El comando termino en ${ESTADO}" >&2
    exit 1
fi
