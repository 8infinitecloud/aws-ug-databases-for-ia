#!/usr/bin/env bash
# =============================================================================
# preflight.sh — ¿tienes todo lo necesario para hacer el workshop?
# =============================================================================
# Se ejecuta ANTES de desplegar nada. No crea recursos y no cuesta dinero.
#
#   ./scripts/preflight.sh
#
# Comprueba herramientas, credenciales, region, acceso a los modelos de Bedrock
# y que el backend de Terraform este listo. Si algo falla, dice exactamente que
# hacer para arreglarlo.
# =============================================================================

set -uo pipefail   # sin -e: queremos ver TODOS los fallos, no solo el primero

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; DIM='\033[2m'; NC='\033[0m'

ERRORS=0
WARNINGS=0

ok()   { echo -e "  ${GREEN}✓${NC} $1${2:+ ${DIM}— $2${NC}}"; }
bad()  { echo -e "  ${RED}✗${NC} $1"; [ -n "${2:-}" ] && echo -e "      ${DIM}→ $2${NC}"; ERRORS=$((ERRORS + 1)); }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; [ -n "${2:-}" ] && echo -e "      ${DIM}→ $2${NC}"; WARNINGS=$((WARNINGS + 1)); }
header() { echo -e "\n${BLUE}$1${NC}"; }

REGION="${AWS_REGION:-us-east-1}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

echo "=============================================================="
echo "  Workshop: De los Datos a la IA — comprobacion previa"
echo "=============================================================="

# ── Herramientas ──────────────────────────────────────────────────────────────
header "1. Herramientas"

if command -v aws >/dev/null 2>&1; then
  AWS_V=$(aws --version 2>&1 | head -1)
  case "$AWS_V" in
    aws-cli/2.*) ok "AWS CLI" "$AWS_V" ;;
    *) warn "AWS CLI v1 detectada" "El workshop se probo con v2: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html" ;;
  esac
else
  bad "AWS CLI no encontrada" "Instala: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
fi

if command -v terraform >/dev/null 2>&1; then
  # Del texto plano y no del JSON: el JSON lleva un espacio tras los dos puntos
  # y es facil escribir un patron que no encaje nunca.
  TF_V=$(terraform version | head -1 | sed -n 's/^Terraform v\([0-9.]*\).*/\1/p')
  TF_V="${TF_V:-desconocida}"
  TF_MAJOR="${TF_V%%.*}"
  TF_MINOR=$(echo "$TF_V" | cut -d. -f2)
  if [ "${TF_MAJOR:-0}" -gt 1 ] 2>/dev/null || { [ "${TF_MAJOR:-0}" -eq 1 ] && [ "${TF_MINOR:-0}" -ge 6 ]; } 2>/dev/null; then
    ok "Terraform" "v$TF_V"
  else
    bad "Terraform v$TF_V es demasiado antigua" "Se necesita >= 1.6: https://developer.hashicorp.com/terraform/downloads"
  fi
else
  bad "Terraform no encontrado" "Instala: https://developer.hashicorp.com/terraform/downloads"
fi

command -v jq >/dev/null 2>&1 \
  && ok "jq" "$(jq --version)" \
  || bad "jq no encontrado" "macOS: brew install jq · Ubuntu: sudo apt install jq"

# El Lab 3 corre Streamlit y el pipeline de ingesta en local.
if command -v python3 >/dev/null 2>&1; then
  PY_V=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
  PY_MINOR=$(echo "$PY_V" | cut -d. -f2)
  if [ "${PY_MINOR:-0}" -ge 11 ] 2>/dev/null; then
    ok "Python" "$PY_V"
  else
    warn "Python $PY_V" "El Lab 3 se probo con 3.11+. Puede fallar con versiones anteriores."
  fi
else
  bad "python3 no encontrado" "Necesario para el Lab 3"
fi

# mysql client: solo lo usa el seed del Lab 1.
command -v mysql >/dev/null 2>&1 \
  && ok "cliente mysql" "$(mysql --version | head -1 | cut -c1-40)" \
  || warn "cliente mysql no encontrado" "El Lab 1 lo usa para cargar el dataset. macOS: brew install mysql-client"

# ── Credenciales AWS ──────────────────────────────────────────────────────────
header "2. Credenciales AWS"

if ! command -v aws >/dev/null 2>&1; then
  bad "Sin AWS CLI no se pueden comprobar las credenciales"
else
  IDENTITY=$(aws sts get-caller-identity --output json 2>/dev/null)
  if [ -z "$IDENTITY" ]; then
    bad "No se pudo autenticar en AWS" "Ejecuta: aws configure"
  else
    ACCOUNT=$(echo "$IDENTITY" | grep -o '"Account": *"[^"]*"' | cut -d'"' -f4)
    ARN=$(echo "$IDENTITY" | grep -o '"Arn": *"[^"]*"' | cut -d'"' -f4)
    ok "Autenticado" "cuenta $ACCOUNT"
    echo -e "      ${DIM}$ARN${NC}"

    CONFIGURED_REGION=$(aws configure get region 2>/dev/null || echo "")
    if [ "$CONFIGURED_REGION" = "$REGION" ]; then
      ok "Region" "$REGION"
    elif [ -z "$CONFIGURED_REGION" ]; then
      warn "Sin region por defecto en la config de AWS" "export AWS_REGION=$REGION"
    else
      warn "Region configurada: $CONFIGURED_REGION (el workshop usa $REGION)" "export AWS_REGION=$REGION"
    fi
  fi
fi

# ── Bedrock ───────────────────────────────────────────────────────────────────
header "3. Modelos de Amazon Bedrock (Lab 3)"

if command -v aws >/dev/null 2>&1; then
  MODELS=$(aws bedrock list-foundation-models --region "$REGION" --output json 2>/dev/null)
  if [ -z "$MODELS" ]; then
    warn "No se pudo consultar Bedrock" "Puede ser falta de permisos. Solo afecta al Lab 3."
  else
    for MODEL in "amazon.titan-embed-text-v2" "amazon.nova-pro-v1"; do
      # Coincidencia con 'case' y no con 'echo | grep -q': bajo pipefail, grep -q
      # cierra la tuberia al primer match, echo recibe SIGPIPE y el pipeline
      # devuelve error aunque el modelo SI estuviera.
      case "$MODELS" in
        *"$MODEL"*) ok "Modelo disponible" "$MODEL" ;;
        *) bad "Modelo NO habilitado: $MODEL" "Consola Bedrock → Model access → Enable. Sin esto el Lab 3 no funciona." ;;
      esac
    done
  fi
fi

# ── Backend de Terraform ──────────────────────────────────────────────────────
header "4. Backend de Terraform"

if [ -f "${REPO_ROOT}/infra/backend.hcl" ]; then
  ok "infra/backend.hcl existe" "backend remoto ya bootstrapeado"
else
  warn "Falta infra/backend.hcl" "Es el primer paso del workshop: cd infra/bootstrap && terraform init && terraform apply"
fi

# ── Resumen ───────────────────────────────────────────────────────────────────
header "Resumen"

if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
  echo -e "  ${GREEN}Todo listo. Puedes empezar por el Lab 1.${NC}"
  echo -e "  ${DIM}→ labs/01-ingestion-data-lake/README.md${NC}"
elif [ "$ERRORS" -eq 0 ]; then
  echo -e "  ${GREEN}Sin bloqueantes${NC} (${WARNINGS} advertencia(s) arriba)."
  echo -e "  ${DIM}Puedes continuar; lee las advertencias por si afectan al lab que te interesa.${NC}"
else
  echo -e "  ${RED}${ERRORS} problema(s) bloqueante(s).${NC} Arreglalos antes de desplegar nada."
  echo -e "  ${DIM}Guia detallada: docs/setup.md${NC}"
fi
echo ""

[ "$ERRORS" -eq 0 ]
