# Trabajar en local, sin AWS

Buena parte de este repositorio se puede ejecutar, probar y modificar sin una
cuenta AWS. Esta página dice exactamente qué, y qué no.

Útil si quieres entender el workshop antes de desplegarlo, contribuir un cambio,
o iterar sobre las reglas de calidad sin esperar a Glue ni pagar nada.

---

## Instalación

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Probado en Python 3.11 y 3.13.

> Las versiones de `pandas`, `numpy` y `pyarrow` van con rango y no con pin
> exacto a propósito: traen binarios compilados y la wheel disponible depende de
> tu versión de Python. Un `pyarrow==17.0.0` fijo rompía la instalación en 3.13.

---

## Lo que funciona sin AWS

### El pipeline completo del Lab 2

Lo más útil. Ejecuta **el mismo `main()`** que corre en AWS, contra un directorio
que hace de bucket:

```bash
python scripts/run_pipeline_local.py --keep
```

Produce lo mismo que el job real:

```
.local-lake/
├── raw/transactions/LOAD00000001.csv       ← como lo dejaría DMS
├── raw/customers/LOAD00000001.csv
├── curated/transactions/year=2024/month=01/part-00000.parquet
│   └── … 33 particiones
├── rejected/transactions/<timestamp>/rejected.csv    ← con rejection_reason
└── quality-reports/latest/quality_report.json
```

Y muestra la tabla que resume la lección del lab:

```
regla                               raw   transf  curated   resuelto por
uniqueness_transaction_id          FAIL    FAIL     PASS     rechazo
validity_transaction_type          FAIL    PASS     PASS     transformación
validity_status                    FAIL    PASS     PASS     transformación
…
filas: 1000 leídas → 900 válidas / 100 rechazadas
```

No es una reimplementación: sustituye el cliente de S3 por un doble que escribe
en disco. El job no nota la diferencia, porque solo usa tres métodos de la API
(`get_object`, `get_paginator`, `put_object`).

**Para qué sirve de verdad:** iterar sobre las reglas de calidad. Cambia un umbral
en `glue_job.py`, vuelve a ejecutar, y ves el efecto en segundos en vez de subir
el script a S3 y esperar a Glue.

```bash
# Inspeccionar el Parquet que salió
python -c "
import pandas as pd, glob
f = glob.glob('.local-lake/curated/**/*.parquet', recursive=True)[0]
print(pd.read_parquet(f).head())
"

# Ver por qué se rechazó cada fila
python -c "
import pandas as pd, glob
f = glob.glob('.local-lake/rejected/**/*.csv', recursive=True)[0]
print(pd.read_csv(f)['rejection_reason'].value_counts())
"
```

### La API de adquirencia, entera

El core de la plataforma corre en local contra un PostgreSQL en Docker. No es una
versión reducida: es el mismo código que va a Fargate, con la misma base y el
mismo esquema.

```bash
docker run -d --name pg-acq -e POSTGRES_PASSWORD=demo -e POSTGRES_DB=acquiring \
  -p 55433:5432 postgres:16.3-alpine
for f in payments/schema/0*.sql; do
  docker exec -i pg-acq psql -U postgres -d acquiring -q -v ON_ERROR_STOP=1 < "$f"
done

pip install -r payments/backend/requirements.txt
cd payments/backend && DB_PORT=55433 DB_PASSWORD=demo uvicorn app.main:app --port 8080
```

Sin `IOT_ENDPOINT` los eventos se escriben en el log en vez de publicarse en IoT
Core, así que el cobro completo —autorizar, capturar, asentar en el libro mayor y
emitir el evento— funciona sin tocar AWS. Queda en `localhost:8080/docs`.

También construye y corre como imagen, que es exactamente lo que ejecuta Fargate:

```bash
docker build -t fincorp-acquiring payments/backend
docker run -p 8099:8080 -e DB_HOST=host.docker.internal -e DB_PORT=55433 \
  -e DB_PASSWORD=demo fincorp-acquiring
```

Si la base no está, `/health` responde `degraded` en vez de morir: un 503 ahí
haría que ECS reciclara la tarea en bucle y el log se perdería con ella.

### Los tests

```bash
pytest tests/ -v
```

68 tests que ejecutan el código real, no dobles:

| | Qué fijan |
|---|---|
| `test_glue_job.py` (49) | los números que citan los README: que en raw fallen reglas, cuáles resuelve la transformación y cuáles el rechazo, y que no se pierda ninguna fila |
| `test_api.py` (19) | que el ledger cuadre, que la idempotencia no permita el doble cobro, y que el evento publicado pase las reglas del Lab 2 |

Los de la API necesitan el PostgreSQL de la sección anterior en el puerto 55433.
**Sin él se saltan**, y el resto de la suite pasa igual — así que si te salen 49
en vez de 68, es que falta la base.

Ese último grupo cubre un fallo que ya ocurrió: el evento salía con
`payment_method: null` y el Lab 2 habría rechazado el 100% de las filas del
stream. Las dos piezas viven en carpetas distintas y nada las ataba.

### El generador del dataset

```bash
# Ver la calibración sin escribir nada
python labs/01-ingestion-data-lake/dataset/generate_dataset.py --check

# Una variante con volumen, para ver dónde se queda corto Python Shell
python labs/01-ingestion-data-lake/dataset/generate_dataset.py --rows 500000
```

### Validar la infraestructura

`terraform validate` y `fmt` **no necesitan credenciales**: comprueban sintaxis,
referencias y tipos contra el esquema del proveedor, que se descarga en el `init`.

```bash
for M in bootstrap payments lab1 lab2 lab3; do
  terraform -chdir="infra/$M" init -backend=false -input=false
  terraform -chdir="infra/$M" validate
done

terraform fmt -check -recursive infra
```

`-backend=false` es la clave: sin él, `init` intenta conectar al backend remoto.

### Los linters

```bash
pip install ruff
ruff check labs/ scripts/ tests/ --ignore E501,F401

# shellcheck: brew install shellcheck  /  apt install shellcheck
shellcheck -x --severity=warning scripts/*.sh labs/*/scripts/*.sh
```

### El escaneo de seguridad

```bash
pip install checkov
checkov -d infra --framework terraform --compact
```

Los hallazgos que quedan abiertos están revisados uno por uno en
[well-architected.md](well-architected.md).

### Los documentos derivados del Lab 2

`scripts/ingest_from_curated.py` sí lee de S3, pero sus funciones de generación
son puras y los tests las cubren. Para verlas en local:

```bash
python -c "
import importlib.util, sys, pandas as pd
from pathlib import Path
def load(n, p):
    s = importlib.util.spec_from_file_location(n, p); m = importlib.util.module_from_spec(s)
    sys.modules[n] = m; s.loader.exec_module(m); return m
gj  = load('glue_job', 'labs/02-transformation-quality-catalog/glue_job.py')
ifc = load('ifc', 'scripts/ingest_from_curated.py')
raw = pd.read_csv('labs/01-ingestion-data-lake/dataset/transactions.csv', dtype=str)
tr, _ = gj.transform_dataframe(raw)
valid, _, _ = gj.apply_data_quality(tr)
print(ifc.doc_risk_profile(valid))
"
```

---

## Lo que necesita credenciales AWS pero no cuesta nada

### `terraform plan`

Consulta al proveedor de verdad: resuelve las zonas de disponibilidad, el AMI vía
SSM y tu account id. Es **solo lectura** y no crea nada, pero necesita
credenciales válidas.

```bash
terraform -chdir=infra/lab1 init -backend-config=../backend.hcl
terraform -chdir=infra/lab1 plan
```

Es la prueba más valiosa después de los tests: `validate` no detecta un error de
esquema del proveedor, `plan` sí.

### `scripts/preflight.sh`

Comprueba herramientas y, si hay credenciales, también el acceso a los modelos de
Bedrock. Sin credenciales informa de lo que no pudo verificar, no se rompe.

---

## Lo que no se puede hacer en local, y por qué

| Componente | Por qué no | Alternativa |
|---|---|---|
| **AWS DMS** (Lab 1) | Es un servicio gestionado sin equivalente local. LocalStack lo emula solo parcialmente y no cubre el CDC sobre binlog, que es lo interesante | Para el Lab 2 no hace falta: `run_pipeline_local.py` siembra `raw/` directamente |
| **Glue Catalog y Crawler** (Lab 2) | El descubrimiento de esquema es del servicio | El Parquet local se puede leer con pandas; el esquema se ve igual |
| **Amazon Bedrock** (Lab 3) | No hay Bedrock local. Los embeddings y el LLM son llamadas al servicio | Ninguna que mantenga el lab igual. Cambiar a un modelo local alteraría lo que el lab enseña |
| **Aurora pgvector, Redis, DynamoDB** (Lab 3) | Sí tienen equivalentes locales (Postgres+pgvector, Redis y DynamoDB Local en Docker), pero sin Bedrock el RAG no funciona de todos modos | — |

> **En una frase:** el Lab 2 es completamente local. El Lab 1 y el Lab 3 dependen
> de servicios gestionados, y ahí no hay atajo honesto.

---

## El "build" del proyecto

No hay compilación: es Python y HCL. Lo que hace de build es lo que corre el CI, y
**todo menos una cosa es local**:

| Job de CI | Local | Necesita AWS |
|-----------|-------|--------------|
| `validate-python` — ruff + sintaxis | ✅ | no |
| `validate-shell` — shellcheck | ✅ | no |
| `test-pipeline` — pytest | ✅ | no (levanta PostgreSQL como servicio) |
| `security-scan` — checkov | ✅ | no |
| `validate-terraform` — fmt + validate | ✅ | no |
| `validate-data` — dataset y corpus | ✅ | no |

Reproducir el CI entero en local:

```bash
pip install -r requirements.txt -r payments/backend/requirements.txt \
  httpx ruff==0.7.0 checkov==3.3.19

ruff check labs/ scripts/ tests/ payments/backend/ --ignore E501,F401
shellcheck -x --severity=warning scripts/*.sh scripts/lib/*.sh labs/*/scripts/*.sh
pytest tests/ -v
checkov -d infra --framework terraform --compact --soft-fail
for M in bootstrap payments lab1 lab2 lab3; do
  terraform -chdir="infra/$M" fmt -check
  terraform -chdir="infra/$M" init -backend=false -input=false
  terraform -chdir="infra/$M" validate
done
```

Si eso pasa, el CI pasa. **Ningún job del CI necesita credenciales AWS** — solo
los workflows de deploy y cleanup, que se ejecutan a mano.

---

## Contribuir sin cuenta AWS

Se puede trabajar en todo esto sin desplegar nada:

- La API de adquirencia entera (`payments/backend/`), con su base en Docker
- Las reglas de calidad y las transformaciones del Lab 2 (`glue_job.py`)
- El dataset y su calibración (`generate_dataset.py`)
- Los documentos derivados (`ingest_from_curated.py`)
- Los tests
- Toda la documentación
- La infraestructura, hasta `validate`

Lo único que exige desplegar es confirmar que DMS completa el full load, que el
CDC funciona, que el Crawler crea la tabla y que el EC2 arranca Streamlit. Esas
cuatro cosas no las verifica ninguna prueba estática.
