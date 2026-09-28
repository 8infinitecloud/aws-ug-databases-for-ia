# API de adquirencia — imagen para Fargate.
FROM python:3.12-slim

# PYTHONUNBUFFERED: sin esto los logs se quedan en el buffer y no llegan a
# CloudWatch hasta que el proceso muere, que es justo cuando hacen falta.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# El contexto de build es `payments/`, no `payments/backend/`, porque la imagen
# necesita tambien la pagina de pago del QR:
#     docker build -f payments/backend/Dockerfile payments/

# Las dependencias en su propia capa: el codigo cambia en cada despliegue, estas
# casi nunca, y asi la reconstruccion no vuelve a bajarlas.
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/app/ ./app/
# La pagina que se abre al escanear el QR. Se sirve desde el mismo origen que la
# API para no necesitar CORS ni un bucket publico.
COPY web/ ./web/

# Sin privilegios. La imagen no necesita root para nada despues del pip install.
RUN useradd --create-home --uid 10001 acquiring
USER acquiring

EXPOSE 8080

# Un solo worker: Fargate escala por tareas, no por procesos dentro de la tarea,
# y asi cada tarea tiene un pool de conexiones predecible contra Aurora.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "1"]
