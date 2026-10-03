# Mercado Andino — la tienda de demo. Corre en local: ver scripts/store_local.sh.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .
COPY web/ ./web/

# Sin privilegios: la tienda no necesita root para nada.
RUN useradd --create-home tienda
USER tienda

EXPOSE 8090
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8090"]
