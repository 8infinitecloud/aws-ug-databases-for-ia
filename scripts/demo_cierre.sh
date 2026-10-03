#!/usr/bin/env bash
# =============================================================================
# demo_cierre.sh — "el pago que hiciste hace 5 minutos está en la respuesta"
# =============================================================================
# El cierre de la charla, de punta a punta:
#
#   pagos de la sala ──▶ PostgreSQL del core ──DMS CDC──▶ raw/acquiring/   (solo, ~30 s)
#                                                              │
#   [1] Glue: aplica los cambios, une el legacy, valida  ──▶ curated/
#   [2] los datos curados se convierten en hechos         ──▶ data/derived/
#   [3] los hechos entran al vector store del Lab 3       ──▶ pgvector
#
# y entonces se le pregunta al asistente:
#   "¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?"
#
# Mide cuánto tarda cada paso: es lo que hay que narrar mientras corre. Si algo
# falla en vivo, el respaldo es la respuesta grabada en el ensayo (ver
# docs/internal/demo-runbook.md), no reintentar delante del público.
#
# Requisitos: Labs 1 y 2 desplegados con la tarea DMS en running, y el .env del
# Lab 3 configurado.
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
REGION="${AWS_REGION:-us-east-1}"
PY="${PYTHON:-python3}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
paso() { echo -e "\n${BLUE}[$1]${NC} $2"; }
INICIO=$(date +%s)
marca() { echo -e "${GREEN}  ✓${NC} $1 ${GREEN}(+$(( $(date +%s) - INICIO )) s)${NC}"; }

tf_init lab2 >/dev/null 2>&1 || true
JOB=$(tf_output lab2 glue_job_name)
[ -n "${JOB}" ] || { echo -e "${RED}[✗]${NC} El Lab 2 no esta desplegado."; exit 1; }

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
      echo -e "${RED}[✗]${NC} El job termino en ${ESTADO}: usa la respuesta de respaldo."; exit 1 ;;
  esac
done
marca "curated/ actualizada"

# ── [2] Hechos ───────────────────────────────────────────────────────────────
paso 2 "Los datos curados, convertidos en hechos citables..."
# ── [3] Vector store ─────────────────────────────────────────────────────────
"${PY}" "${REPO_ROOT}/scripts/ingest_from_curated.py" --region "${REGION}" --ingest >/dev/null
marca "hechos generados e ingestados en pgvector"

echo
echo -e "${GREEN}  Listo en $(( $(date +%s) - INICIO )) s.${NC} Pregúntale al asistente:"
echo
echo '    "¿Cuántos pagos se procesaron hoy y con qué tasa de aprobación?"'
echo
