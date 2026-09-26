# Datasets — el del workshop y alternativas reales

El workshop usa un dataset sintético de 1000 transacciones. Esta página explica
**por qué**, y qué datasets reales puedes usar en su lugar si quieres llevar el
lab más allá.

---

## Por qué el workshop usa datos sintéticos

No es pereza: es que un dataset real no sirve para enseñar calidad de datos de
forma predecible.

El Lab 2 mide 8 reglas y cada una tiene que comportarse de una manera concreta
para que la lección funcione:

| Problema | Cantidad exacta | Para que el participante vea… |
|----------|-----------------|-------------------------------|
| `amount` vacío | 50 de 1000 (5.0%) | `Completeness = 0.9500` contra un umbral `>= 0.95`: **pasa por un pelo**. Un umbral es una decisión, no una verdad. |
| `transaction_id` duplicado | 55 filas | Uniqueness falla y **no se puede arreglar transformando**: ante dos filas con el mismo id, nadie puede inventar cuál es la buena. Se rechaza. |
| Tipo en español | 20 (2.0%) | Falla en raw y **la transformación lo resuelve**: `DEPÓSITO` es claramente `DEPOSIT`. El dato era recuperable. |
| `status` con capitalización inconsistente | 80 (8.0%) | Igual que el anterior, vía `.upper()`. |
| Fecha en `DD/MM/YYYY` | 100 (10.0%) | No es una regla DQDL: lo arregla la transformación y se ve en `transform_stats`. |

Con un dataset real no controlas nada de esto. Podrías acabar con un dataset
impecable (y el Lab 2 sin nada que enseñar) o con uno tan roto que el pipeline
falle por razones que distraen del tema.

**La calibración es auditable**, no magia:

```bash
python labs/01-ingestion-data-lake/dataset/generate_dataset.py --check
```

Ese script documenta en código cada porcentaje y por qué está ahí. Los CSV del
repositorio son la referencia y `tests/test_glue_job.py` los verifica.

> El dataset es sintético, pero **los problemas son reales**. Importes nulos
> porque el sistema origen los permitía, tipos en dos idiomas porque dos equipos
> nombraron lo mismo distinto, IDs duplicados por un proceso de carga sin
> idempotencia, fechas en dos formatos por el locale de un servidor. Nada de eso
> está inventado.

---

## Alternativas reales

Ordenadas por lo bien que encajan con la narrativa del workshop.

### 1. PaySim — transacciones financieras sintéticas con fraude etiquetado

**El mejor encaje si quieres volumen sin perder el dominio financiero.**

| | |
|---|---|
| Qué es | ~6.3 millones de transacciones de dinero móvil, 744 pasos de 1 hora (30 días simulados) |
| Origen | Simulador PaySim, calibrado sobre un mes de logs reales de un servicio de dinero móvil africano |
| Columnas | `step`, `type` (CASH_IN, CASH_OUT, DEBIT, PAYMENT, TRANSFER), `amount`, `nameOrig`, `nameDest`, balances antes/después, `isFraud`, `isFlaggedFraud` |
| Fraude | 8 213 casos = 0.13% — desbalance de clase realista |
| Dónde | [Kaggle: ealaxi/paysim1](https://www.kaggle.com/datasets/ealaxi/paysim1) |
| Descarga | Requiere cuenta de Kaggle |

**Por qué encaja:** es transaccional y financiero, así que la historia de FinCorp
se mantiene entera. Y añade algo que el dataset del workshop no tiene: **volumen
suficiente para que la decisión Python Shell vs PySpark importe de verdad**. Con
6 millones de filas, pandas en 0.0625 DPU ya no es la respuesta correcta, y eso
es una lección por sí misma.

**Lo que hay que ajustar:** PaySim viene *limpio*. Si lo usas tal cual, el Lab 2
no rechaza nada. Tendrías que inyectar problemas — y para eso ya tienes
`generate_dataset.py` como referencia de cómo hacerlo de forma calibrada.

---

### 2. NYC TLC Trip Records — el dataset abierto de AWS mejor documentado

| | |
|---|---|
| Qué es | Viajes de taxis y VTC en Nueva York, desde 2009 |
| Formato | Parquet, ya particionado por año y mes |
| Bucket | `s3://nyc-tlc/` (`arn:aws:s3:::nyc-tlc`, región `us-east-1`) |
| Coste | Gratis. El acceso directo por S3 requiere cuenta AWS; también hay descarga anónima desde la web de TLC. Parte del Open Data Sponsorship Program de AWS. |
| Licencia | [Términos de uso de NYC](http://www1.nyc.gov/home/terms-of-use.page) |
| Registro | [registry.opendata.aws/nyc-tlc-trip-records-pds](https://registry.opendata.aws/nyc-tlc-trip-records-pds/) |

**Por qué encaja:** está **en S3 y en Parquet ya particionado**, así que puedes
saltarte el Lab 1 y empezar directamente por el Glue Crawler y Athena. Es el
camino más corto para ver el catálogo funcionando sobre datos de verdad.

**Lo que no encaja:** no es financiero, así que se pierde la narrativa de
compliance del Lab 3. Y está muy usado en tutoriales: como demo no sorprende a
nadie.

---

### 3. Datos abiertos del Estado peruano — el ángulo local

**El más interesante para un AWS Community Day Perú**, y el que menos se usa.

| | |
|---|---|
| Qué es | 4 656 datasets y 17 149 recursos de 400 entidades públicas |
| Formatos | CSV, XLSX, DOCX, y geoespaciales (GeoJSON, KML, GeoTIFF) |
| Dónde | [datosabiertos.gob.pe](https://www.datosabiertos.gob.pe/) |
| Descarga | Sin registro |

Ejemplos concretos del catálogo, verificados:

- **Empresas agroindustriales a nivel nacional** — PRODUCE (CSV, XLSX)
- **Supervisiones y clasificación de riesgo en el comercio legal de madera** — OSINFOR (CSV, XLSX)
- **Resultados del proceso de admisión de pregrado** — UNMSM (CSV, XLSX, PDF)
- Indicadores de educación rural y de educación intercultural bilingüe
- Estadísticas de enfermedades ocupacionales en minería

También hay portales sectoriales:

- [datosabiertos.mef.gob.pe](https://datosabiertos.mef.gob.pe/) — Ministerio de Economía y Finanzas: gasto público
- [sinia.minam.gob.pe](https://sinia.minam.gob.pe/portal/datos-abiertos/) — Ministerio del Ambiente
- [ONPE](https://www.gob.pe/61986-acceder-a-datos-abiertos) — datos electorales

**Por qué encaja:** la audiencia se reconoce en los datos. Y tiene una ventaja
que ningún dataset curado tiene: **los datos abiertos del Estado vienen sucios de
verdad**. Encoding inconsistente, nombres de columna con tildes y espacios,
fechas en varios formatos, celdas vacías. No hay que inyectar nada: el problema
ya está ahí.

**Lo que hay que asumir:** precisamente por eso, la calibración desaparece. No
sabes de antemano qué reglas van a fallar. Para una charla con tiempo medido eso
es un riesgo; para un taller largo o para practicar en serio, es lo más honesto
que puedes usar.

> **La "clasificación de riesgo" de OSINFOR es el encaje más limpio** si quieres
> mantener el Lab 3: ya trae un concepto de riesgo, que es justo lo que el
> asistente de compliance consulta.

---

### 4. IEEE-CIS Fraud Detection — para irse a ML

| | |
|---|---|
| Qué es | Transacciones de e-commerce reales de Vesta, con fraude etiquetado |
| Tamaño | ~590 000 transacciones, 393 columnas |
| Dónde | [Kaggle: ieee-fraud-detection](https://www.kaggle.com/c/ieee-fraud-detection) |

**Por qué se menciona:** con 393 columnas, la mayoría anonimizadas, el problema
deja de ser "limpiar datos" y pasa a ser **feature engineering y catálogo**.
Descubrir qué significa cada columna sin documentación es exactamente el problema
que resuelve un Data Catalog con metadata de negocio, que es el Lab 2.

**Lo que no encaja:** demasiado grande y demasiado anónimo para un lab de 15
minutos.

---

### Otras fuentes revisadas

- **[Registry of Open Data on AWS](https://registry.opendata.aws/)** — el
  catálogo general. Datasets de alto valor ya en S3, con el almacenamiento
  pagado por AWS. No encontré ningún dataset financiero transaccional que
  encajara con la narrativa; casi todo es científico, geoespacial o genómico.
- **MoMTSim** — simulador multiagente de dinero móvil, más nuevo que PaySim.
  Interesante si quieres generar tu propio dataset con propiedades
  estadísticas controladas.
- **Amazon Fraud Detector** — AWS publica datasets sintéticos de ejemplo en sus
  tutoriales de fraude. Pequeños, pero pensados para demos.

---

## Cómo cambiar el dataset

Lo que toca, en orden:

### 1. El esquema en MySQL

`labs/01-ingestion-data-lake/dataset/seed.sql` define las tablas. Si el dataset
nuevo tiene otras columnas, cambia el `CREATE TABLE`.

### 2. El mapeo de tablas de DMS

`infra/lab1/dms.tf`, en `table_mappings`, selecciona `transactions` y
`customers` por nombre. Ajusta los `object-locator`.

### 3. El ETL

`labs/02-transformation-quality-catalog/glue_job.py`:

- `VALID_TRANSACTION_TYPES`, `VALID_STATUSES`, `VALID_CURRENCIES` — los valores permitidos
- `TYPE_NORMALIZATION` — el mapeo español → inglés
- `transform_dataframe()` — qué columnas se normalizan
- `measure_rules()` — las 8 reglas, por nombre de columna

### 4. El ruleset declarado

`labs/02-transformation-quality-catalog/quality_rules.dqdl` y su `.json`. Y el
bloque `ruleset` en `infra/lab2/glue.tf`, que los duplica: **los tres tienen que
decir lo mismo**.

### 5. Los tests y los números documentados

```bash
pytest tests/ -v
```

Van a fallar, y eso es lo que se espera: fijan que sean 100 filas rechazadas y
que la calidad vaya de 5/8 a 8/8. Actualízalos con los números nuevos, y con
ellos las tablas de `labs/02-transformation-quality-catalog/README.md` y del
README raíz.

> **Este es el orden que importa:** si cambias el dataset y no actualizas los
> tests, el workshop sigue funcionando pero su documentación miente. Los tests
> están ahí justamente para que no pase en silencio.

---

## Si solo quieres más volumen

No hace falta cambiar de dataset. El generador escala manteniendo la calibración:

```bash
python labs/01-ingestion-data-lake/dataset/generate_dataset.py \
  --rows 500000 --customers 10000
```

Los porcentajes de cada problema se mantienen, así que las reglas del Lab 2 se
comportan igual. Con medio millón de filas empezarás a notar que Python Shell a
0.0625 DPU se queda corto — y ese es el momento de hablar de PySpark.

Después, actualiza los tests: el número absoluto de filas rechazadas cambia
aunque el porcentaje no.

---

## Fuentes

- [Registry of Open Data on AWS](https://registry.opendata.aws/)
- [NYC TLC Trip Record Data — Registry of Open Data](https://registry.opendata.aws/nyc-tlc-trip-records-pds/)
- [PaySim: Synthetic Financial Datasets For Fraud Detection (Kaggle)](https://www.kaggle.com/datasets/ealaxi/paysim1)
- [Plataforma Nacional de Datos Abiertos del Perú](https://www.datosabiertos.gob.pe/)
- [Portal de Datos Abiertos del MEF](https://datosabiertos.mef.gob.pe/)
- [Datos Abiertos SINIA — MINAM](https://sinia.minam.gob.pe/portal/datos-abiertos/)
- [Acceder a datos abiertos — ONPE](https://www.gob.pe/61986-acceder-a-datos-abiertos)
- [A labeled synthetic mobile money transaction dataset (MoMTSim)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC12036017/)
- [Detect financial transaction fraud using a Graph Neural Network with Amazon SageMaker](https://aws.amazon.com/blogs/machine-learning/detect-financial-transaction-fraud-using-a-graph-neural-network-with-amazon-sagemaker/)
