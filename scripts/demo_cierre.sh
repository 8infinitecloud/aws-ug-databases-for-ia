#!/usr/bin/env bash
# =============================================================================
# demo_cierre.sh — del pago a la respuesta del asistente
# =============================================================================
# Lleva los pagos recientes hasta el RAG del Lab 3:
#
#   pagos ──▶ PostgreSQL del core ──DMS CDC──▶ raw/acquiring/   (solo, ~30 s)
#                                                              │
#   [1] Glue: aplica los cambios, une el legacy, valida  ──▶ curated/
#   [2] los datos curados se convierten en hechos         ──▶ data/derived/   ┐ en el EC2
#   [3] los hechos entran al vector store del Lab 3       ──▶ pgvector        ┘ del Lab 3
#
# y entonces se le pregunta al asistente:
#   "¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?"
#
# Imprime cuánto tarda cada paso.
#
# Los pasos 2 y 3 corren en el EC2 del Lab 3, por SSM Run Command: Aurora esta
# en una subnet privada y el EC2 la alcanza; esta maquina no. Aqui solo se
# orquesta: no hace falta el .env del Lab 3, ni tunel, ni session-manager-plugin.
#
# Requisitos: Labs 1, 2 y 3 desplegados (el 3 con deploy-app.sh hecho) y la
# tarea DMS en running.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
REGION="${AWS_REGION:-us-east-1}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
paso() { echo -e "\n${BLUE}[$1]${NC} $2"; }
INICIO=$(date +%s)
marca() { echo -e "${GREEN}  ✓${NC} $1 ${GREEN}(+$(( $(date +%s) - INICIO )) s)${NC}"; }

for lab in lab1 lab2 lab3; do tf_init "${lab}" >/dev/null 2>&1 || true; done
JOB=$(tf_output lab2 glue_job_name)
BUCKET=$(tf_output lab1 s3_bucket_name)
EC2=$(tf_output lab3 app_server_instance_id)
[ -n "${JOB}" ]    || { echo -e "${RED}[✗]${NC} El Lab 2 no esta desplegado."; exit 1; }
[ -n "${BUCKET}" ] || { echo -e "${RED}[✗]${NC} El Lab 1 no esta desplegado."; exit 1; }
[ -n "${EC2}" ]    || { echo -e "${RED}[✗]${NC} El Lab 3 no esta desplegado."; exit 1; }

# ── [1] Glue ─────────────────────────────────────────────────────────────────
paso 1 "Glue: del CDC del core a la zona curada..."
RUN=$(aws glue start-job-run --job-name "${JOB}" --region "${REGION}" --query JobRunId --output text)
while true; do
  sleep 5
  ESTADO=$(aws glue get-job-run --job-name "${JOB}" --run-id "${RUN}" --region "${REGION}" \
             --query JobRun.JobRunState --output text)
  case "${ESTADO}" in
    SUCCEEDED) break ;;
    FAILED|ERROR|TIMEOUT|STOPPED)
      echo -e "${RED}[✗]${NC} El job termino en ${ESTADO}. Log: /aws-glue/python-jobs/error (run ${RUN})"; exit 1 ;;
  esac
done
marca "curated/ actualizada"

# ── [2] Hechos y [3] vector store, en el EC2 del Lab 3 ──────────────────────
paso "2-3" "Los datos curados, convertidos en hechos e ingestados en pgvector..."
# El script viaja con la app (deploy-app.sh). El .env del EC2 trae Aurora,
# Bedrock y la region; el bucket se le pasa porque alli no hay Terraform.
ORDEN="cd /opt/rag-lab && set -a && . ./.env && set +a && python3 scripts/ingest_from_curated.py --bucket ${BUCKET} --region ${REGION} --ingest"
CMD=$(aws ssm send-command --instance-ids "${EC2}" --document-name AWS-RunShellScript \
        --parameters "$(jq -n --arg c "${ORDEN}" '{commands: [$c]}')" \
        --region "${REGION}" --query Command.CommandId --output text)
while true; do
  sleep 3
  ESTADO=$(aws ssm get-command-invocation --command-id "${CMD}" --instance-id "${EC2}" \
             --region "${REGION}" --query Status --output text 2>/dev/null || echo Pending)
  case "${ESTADO}" in
    Success) break ;;
    Pending|InProgress|Delayed) ;;
    *)
      aws ssm get-command-invocation --command-id "${CMD}" --instance-id "${EC2}" \
        --region "${REGION}" --query StandardErrorContent --output text | tail -5
      echo -e "${RED}[✗]${NC} La ingesta termino en ${ESTADO} (comando SSM ${CMD} en ${EC2})."; exit 1 ;;
  esac
done
marca "hechos generados e ingestados en pgvector"

echo
echo -e "${GREEN}  Listo en $(( $(date +%s) - INICIO )) s.${NC} Pregúntale al asistente:"
echo
echo '    "¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?"'
echo
