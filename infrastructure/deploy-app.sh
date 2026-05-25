#!/bin/bash
# deploy-app.sh — Despliega (o actualiza) la aplicación en el EC2 App Server.
#
# Completamente independiente de setup.sh. Requisito: el stack de infraestructura
# ya debe existir (haber corrido setup.sh al menos una vez).
#
# Cuándo usar:
#   - Primera vez después de setup.sh
#   - Cada vez que cambies código de la app (app/, ingestion/, query/, data/)
#   - Para re-ingestar documentos con --reingest
#
# Tiempo estimado: 3-5 minutos.

set -euo pipefail

STACK_NAME="financial-compliance-rag-lab"
REGION="${AWS_REGION:-us-east-1}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

# Flag para forzar re-ingesta (borra la tabla y vuelve a ingestar)
REINGEST=false
if [[ "${1:-}" == "--reingest" ]]; then
  REINGEST=true
fi

echo "============================================================"
echo "  FinCorp RAG Lab — Deploy de Aplicación"
echo "============================================================"
echo "  Stack:    $STACK_NAME"
echo "  Región:   $REGION"
echo "  Reingest: $REINGEST"
[ -n "${AWS_PROFILE:-}" ] && echo "  Perfil:   $AWS_PROFILE"
echo "============================================================"

# ─────────────────────────────────────────────────────────────────────────────
# [1/4] Leer outputs del stack de infraestructura
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[1/4] Leyendo configuración del stack..."

get_output() {
  aws cloudformation describe-stacks \
    --stack-name "$STACK_NAME" \
    --region "$REGION" \
    --query "Stacks[0].Outputs[?OutputKey=='$1'].OutputValue" \
    --output text 2>/dev/null || echo ""
}

S3_BUCKET=$(get_output "S3BucketName")
INSTANCE_ID=$(get_output "AppServerInstanceId")
STREAMLIT_URL=$(get_output "StreamlitURL")

if [ -z "$S3_BUCKET" ] || [ -z "$INSTANCE_ID" ]; then
  echo "❌ No se encontró el stack '$STACK_NAME'."
  echo "   Ejecuta primero: ./setup.sh"
  exit 1
fi

echo "  ✓ S3:        $S3_BUCKET"
echo "  ✓ EC2:       $INSTANCE_ID"
echo "  ✓ Streamlit: $STREAMLIT_URL"

# ─────────────────────────────────────────────────────────────────────────────
# [2/4] Empaquetar código y subir a S3
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[2/4] Empaquetando y subiendo código a S3..."

tar -czf /tmp/rag-lab-app.tar.gz \
  -C "$ROOT_DIR" \
  --exclude='.git' \
  --exclude='.env' \
  --exclude='*.pptx' \
  --exclude='__pycache__' \
  --exclude='*.pyc' \
  --exclude='.venv' \
  --exclude='infrastructure' \
  app/ ingestion/ query/ scripts/ data/ requirements.txt

aws s3 cp /tmp/rag-lab-app.tar.gz "s3://$S3_BUCKET/app-code.tar.gz" --region "$REGION"
aws s3 sync "$ROOT_DIR/data/" "s3://$S3_BUCKET/compliance-docs/" \
  --region "$REGION" --exclude "*.DS_Store"
rm /tmp/rag-lab-app.tar.gz

echo "  ✓ Código subido"

# ─────────────────────────────────────────────────────────────────────────────
# [3/4] Esperar que el EC2 esté registrado en SSM (solo si acaba de arrancar)
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[3/4] Verificando conexión SSM al EC2..."

MAX_WAIT=180
ELAPSED=0
while [ $ELAPSED -lt $MAX_WAIT ]; do
  SSM_STATUS=$(aws ssm describe-instance-information \
    --region "$REGION" \
    --filters "Key=InstanceIds,Values=$INSTANCE_ID" \
    --query "InstanceInformationList[0].PingStatus" \
    --output text 2>/dev/null || echo "None")

  if [ "$SSM_STATUS" = "Online" ]; then
    echo "  ✓ EC2 disponible via SSM"
    break
  fi

  echo "  ⏳ SSM status: $SSM_STATUS — esperando..."
  sleep 15
  ELAPSED=$((ELAPSED + 15))

  if [ $ELAPSED -ge $MAX_WAIT ]; then
    echo "❌ EC2 no registrado en SSM después de 3 min."
    echo "   Revisa en: AWS Console → Systems Manager → Fleet Manager"
    exit 1
  fi
done

# ─────────────────────────────────────────────────────────────────────────────
# [4/4] Ejecutar deploy en EC2 via SSM Run Command
# ─────────────────────────────────────────────────────────────────────────────

echo ""
echo "[4/4] Ejecutando deploy en EC2 via SSM..."

# Construir el script que correrá DENTRO del EC2.
# Placeholders __VAR__ se reemplazan con sed antes de subir a S3,
# así las variables del EC2 ($APP_DIR, etc.) no son expandidas localmente.
cat > /tmp/deploy-app-remote.sh << 'REMOTE_EOF'
#!/bin/bash
set -ex
exec > >(tee /var/log/deploy-app.log) 2>&1

APP_DIR="/opt/rag-lab"
S3_BUCKET="__S3_BUCKET__"
REGION="__REGION__"
REINGEST="__REINGEST__"

echo "=== Descargando código de S3 ==="
aws s3 cp "s3://$S3_BUCKET/app-code.tar.gz" "$APP_DIR/app-code.tar.gz" --region "$REGION"
cd "$APP_DIR"
tar -xzf app-code.tar.gz --overwrite
rm app-code.tar.gz

if [ ! -f "$APP_DIR/.env" ]; then
  echo "ERROR: .env no encontrado. El UserData del EC2 no completó correctamente."
  exit 1
fi

echo "=== Inicializando schema de pgvector ==="
python3 "$APP_DIR/scripts/init_db.py"

echo "=== Ingestando documentos ==="
if [ "$REINGEST" = "true" ]; then
  python3 "$APP_DIR/scripts/ingest_documents.py" --source "$APP_DIR/data/" --clear
else
  python3 "$APP_DIR/scripts/ingest_documents.py" --source "$APP_DIR/data/"
fi

echo "=== Reiniciando Streamlit ==="
pkill -f "streamlit run" 2>/dev/null || true
sleep 2

nohup python3 -m streamlit run "$APP_DIR/app/app.py" \
  --server.port 8501 \
  --server.headless true \
  --server.address 0.0.0.0 \
  >> /var/log/streamlit.log 2>&1 &

sleep 3
if pgrep -f "streamlit run" > /dev/null; then
  echo "=== Streamlit OK en puerto 8501 ==="
else
  echo "ERROR: Streamlit no inició. Ver /var/log/streamlit.log"
  cat /var/log/streamlit.log | tail -20
  exit 1
fi
REMOTE_EOF

# Sustituir los placeholders con valores reales
# sed -i '' funciona en macOS (BSD sed) y Linux (GNU sed)
sed -i '' "s|__S3_BUCKET__|$S3_BUCKET|g" /tmp/deploy-app-remote.sh
sed -i '' "s|__REGION__|$REGION|g"       /tmp/deploy-app-remote.sh
sed -i '' "s|__REINGEST__|$REINGEST|g"   /tmp/deploy-app-remote.sh

# Subir el script al S3 del lab y ejecutarlo via SSM con un único comando simple.
# No se pasa el script inline porque SSM Run Command no soporta bien multi-línea en --parameters.
aws s3 cp /tmp/deploy-app-remote.sh "s3://$S3_BUCKET/deploy-app-remote.sh" --region "$REGION"
rm /tmp/deploy-app-remote.sh

COMMAND_ID=$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name "AWS-RunShellScript" \
  --parameters "commands=[\"aws s3 cp s3://$S3_BUCKET/deploy-app-remote.sh /tmp/deploy-remote.sh --region $REGION && chmod +x /tmp/deploy-remote.sh && /tmp/deploy-remote.sh\"]" \
  --timeout-seconds 600 \
  --query "Command.CommandId" \
  --output text)

echo "  SSM Command ID: $COMMAND_ID"
echo "  Esperando resultado..."

# aws ssm wait command-executed tiene un timeout corto en algunas versiones del CLI;
# si falla, verificamos el status manualmente.
aws ssm wait command-executed \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --region "$REGION" 2>/dev/null || true

COMMAND_STATUS=$(aws ssm get-command-invocation \
  --command-id "$COMMAND_ID" \
  --instance-id "$INSTANCE_ID" \
  --region "$REGION" \
  --query "Status" \
  --output text)

echo "  Status del comando: $COMMAND_STATUS"

if [ "$COMMAND_STATUS" = "Success" ]; then
  echo ""
  echo "============================================================"
  echo "  ✅ App desplegada y Streamlit corriendo"
  echo "============================================================"
  echo ""
  echo "  🌐 Abre en tu navegador:"
  echo "     $STREAMLIT_URL"
  echo ""
  echo "  📋 Logs en tiempo real:"
  echo "     aws ssm start-session --target $INSTANCE_ID --region $REGION"
  echo "     # dentro del EC2: tail -f /var/log/streamlit.log"
  echo ""
else
  echo ""
  echo "  ⚠️  El deploy terminó con status: $COMMAND_STATUS"
  echo "  Ver output completo:"
  echo "  aws ssm get-command-invocation \\"
  echo "    --command-id $COMMAND_ID \\"
  echo "    --instance-id $INSTANCE_ID \\"
  echo "    --region $REGION \\"
  echo "    --query 'StandardOutputContent'"
  exit 1
fi
