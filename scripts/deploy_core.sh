#!/usr/bin/env bash
# =============================================================================
# deploy_core.sh — levanta el core de pagos: infra + imagen del backend
# =============================================================================
# El core es lo primero que existe y todo lo demas cuelga de el. Hasta ahora
# desplegarlo eran pasos a mano (apply, build, push, apply otra vez), y en el
# primer despliegue real fallo por cosas que solo aparecen ahi. Esto los hace
# en orden y espera a que la API responda con la base lista.
#
#   1. terraform apply de infra/payments (la base arranca y aplica su esquema)
#   2. construye la imagen del backend para linux/amd64 (Fargate es x86 aqui,
#      aunque tu maquina sea ARM) y la sube a ECR con el commit como tag
#   3. apply otra vez con esa imagen: el servicio pasa de 0 a 1 tarea
#   4. espera a que /health diga database=ok
#
# Uso:
#   export TF_VAR_db_password='...'
#   ./scripts/deploy_core.sh
# =============================================================================
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/tf.sh
source "${REPO_ROOT}/scripts/lib/tf.sh"
REGION="${AWS_REGION:-us-east-1}"

BLUE='\033[0;34m'; GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
info()  { echo -e "${BLUE}[INFO]${NC} $*"; }
ok()    { echo -e "${GREEN}[✓]${NC} $*"; }
error() { echo -e "${RED}[✗]${NC} $*" >&2; exit 1; }

[ -n "${TF_VAR_db_password:-}" ] || error "Falta la contrasena de la base: export TF_VAR_db_password='...'"
command -v docker >/dev/null || error "Falta docker para construir la imagen del backend."
docker info >/dev/null 2>&1 || error "El daemon de docker no responde. ¿Docker Desktop esta abierto?"
tf_require || error "Terraform no esta listo. Ver infra/README.md."
export TF_VAR_aws_region="${REGION}"

info "[1/4] infra/payments (red, base en EC2, ECR, ALB, API Gateway)..."
tf_init payments
# Si ya habia una imagen desplegada, se conserva en este primer apply. Por -json
# y no -raw: con el state vacio, -raw no devuelve vacio sino un aviso de varias
# lineas POR STDOUT, que acababa como nombre de la imagen ("Container.image
# should be 255 characters or less"). Y solo se acepta algo con forma de imagen.
IMAGEN_ACTUAL=$(terraform -chdir="${TF_INFRA_DIR}/payments" output -json backend_image 2>/dev/null \
                  | jq -r 'select(type == "string")' 2>/dev/null || true)
[[ "${IMAGEN_ACTUAL}" =~ ^[a-z0-9.-]+/[a-z0-9._/-]+:[A-Za-z0-9._-]+$ ]] || IMAGEN_ACTUAL=""
tf_apply payments -var "backend_image=${IMAGEN_ACTUAL}" >/dev/null
REPO=$(tf_output payments ecr_repository_url)
ok "Infraestructura aplicada"

info "[2/4] Imagen del backend..."
TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)$(git -C "${REPO_ROOT}" diff --quiet || echo -dirty)"
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${REPO%%/*}" >/dev/null
# El contexto es payments/ y no payments/backend/: la imagen lleva tambien web/.
docker build --platform linux/amd64 -t "${REPO}:${TAG}" \
  -f "${REPO_ROOT}/payments/backend/Dockerfile" "${REPO_ROOT}/payments" >/dev/null
docker push "${REPO}:${TAG}" >/dev/null
ok "${REPO}:${TAG}"

info "[3/4] Desplegando la imagen en Fargate..."
tf_apply payments -var "backend_image=${REPO}:${TAG}" >/dev/null
aws ecs wait services-stable --region "${REGION}" \
  --cluster "$(tf_output payments ecs_cluster_name)" \
  --services "$(tf_output payments ecs_service_name)"
ok "Servicio estable"

info "[4/4] Esperando a que la API vea la base..."
API=$(tf_output payments api_endpoint)
# La base instala PostgreSQL y aplica el esquema en su primer arranque: unos
# minutos. Mientras, /health responde degraded en vez de tirar el contenedor.
for _ in $(seq 1 60); do
  ESTADO=$(curl -fsS "${API}/health" 2>/dev/null | jq -r .database 2>/dev/null || echo "-")
  [ "${ESTADO}" = "ok" ] && break
  echo -n "."
  sleep 10
done
echo
[ "${ESTADO}" = "ok" ] || error "La API no ve la base. Log del arranque: /var/log/fincorp-db-init.log en la instancia (Session Manager)."
ok "API lista: ${API}"

cat <<FIN

  El core esta arriba.

    API            ${API}
    Pagina del QR  ${API}/pay/<referencia>

  Siguiente:
    python scripts/provision_demo_keys.py --ssm      # claves de las tiendas online
    python scripts/generate_traffic.py --api ${API} --count 50
    cd labs/01-ingestion-data-lake/scripts && ./setup.sh

FIN
