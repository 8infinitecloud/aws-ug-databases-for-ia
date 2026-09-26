# Casos reales

Tres empresas que construyeron la plataforma de datos antes de construir la IA.
Cada una mapea a un lab del workshop.

> Todas las cifras están verificadas contra fuentes oficiales de AWS, enlazadas
> al final de cada caso. Si una cifra no tiene fuente, no está aquí.

---

## AJE Group — el caso peruano

**El más relevante para esta audiencia, y el que menos se cuenta.**

AJE es una multinacional peruana de bebidas con 35 años de historia y operaciones
en más de 20 países de Latinoamérica, Asia y África. Una de las mayores empresas
de bebidas del mundo, fundada en Ayacucho.

### El problema

Infraestructura on-premises aislada, repartida entre varias ubicaciones
geográficas. Los equipos internos **esperaban entre 4 y 5 horas** para obtener
los datos que necesitaban para decidir.

Ese es el problema del Lab 1, sin adornos: datos que existen pero no están
accesibles donde hacen falta.

### Lo que construyeron

```
Bases de datos SQL heredadas, en varios países
    │
    ▼  AWS DMS
Amazon S3
    │
    ▼  AWS Glue
Amazon Redshift  ← "nuestro repositorio universal, la única fuente de verdad"
```

Migraron **más de 70 aplicaciones, 300 servidores y 700 TB** de información a la
nube.

### Resultados

| Métrica | Resultado |
|---------|-----------|
| Tiempos de ETL | **−35%** |
| Costes de infraestructura | **−15%** |
| Velocidad de entrega de datos a los equipos | **+20%** |
| Disponibilidad de datos | de 4–5 horas de espera a **casi tiempo real** |

> *"Amazon Redshift es ahora nuestro repositorio universal, esencialmente la
> única fuente de verdad dentro de la compañía."*
> — Wilmer Rodriguez Ruiz, Head of Global Data and Analytics, AJE Group

### Conexión con el workshop

| Lo que hizo AJE | Nuestro lab |
|-----------------|-------------|
| DMS desde SQL heredado hacia S3 | **Lab 1** — patrón idéntico |
| Glue para el ETL | **Lab 2** |
| Redshift como destino analítico | Lab 2, donde usamos Athena (serverless, sin cluster) |
| Bedrock sobre la plataforma ya construida | **Lab 3** — la IA llega después, no antes |

**Lo que hace este caso especial:** AJE no empezó por la IA. Empezó por el
problema de tener datos en veinte países sin visión consolidada. La IA llegó
cuando la plataforma existía. Es exactamente la tesis del workshop, contada por
una empresa peruana.

**Fuente:** [AWS Case Study — AJE Group](https://aws.amazon.com/solutions/case-studies/aje-case-study/)

---

## BMW Group — la misma arquitectura, a escala de petabytes

### El problema

Datos repartidos por toda la organización: sensores de vehículos, manufactura,
ventas, desarrollo. Cada área con su silo, y sin una zona común donde aterrizar
lo que produce cada una.

### Lo que construyeron

El **Cloud Data Hub (CDH)**, con AWS desde 2018. Almacena **más de 20 PB** e
ingesta una media de **110 TB al día**.

```
Fuentes: sensores de vehículos, manufactura, ventas, desarrollo
    │
    ▼  AWS Glue · Amazon EMR · Amazon Kinesis · AWS Lambda
Amazon S3  ── AWS Glue Data Catalog
    │
    ├──▶ Amazon Athena          (consulta)
    ├──▶ AWS Lake Formation     (permisos a nivel de fila y columna)
    └──▶ Amazon SageMaker · Amazon QuickSight · Amazon Bedrock
```

### Conexión con el workshop

| Lo que hizo BMW | Nuestro lab |
|-----------------|-------------|
| S3 como zona de aterrizaje neutral | **Lab 1** |
| Glue para transformar | **Lab 2** |
| Glue Data Catalog como índice común | **Lab 2** |
| Lake Formation para governance | Lab 2, mencionado como extensión natural |
| Bedrock y SageMaker sobre la plataforma | **Lab 3** |

**La lección:** la arquitectura del workshop y la de BMW son la misma. Lo que
cambia es la escala — 20 PB frente a 1000 filas — y eso significa que los
patrones no dependen del tamaño. Lo que cambia con el tamaño es la herramienta:
donde nosotros usamos Glue Python Shell, BMW usa EMR y Kinesis.

**Fuentes:**
[BMW Cloud Data Hub — reference implementation (AWS Blog)](https://aws.amazon.com/blogs/industries/bmw-cloud-data-hub-a-reference-implementation-of-the-modern-data-architecture-on-aws/) ·
[BMW Group en AWS](https://aws.amazon.com/solutions/case-studies/innovators/bmw/) ·
[BMW Group unlocks insights from petabytes of data with agentic search](https://aws.amazon.com/blogs/industries/bmw-group-unlocks-insights-from-petabytes-of-data-with-agentic-search-on-aws/)

---

## United Airlines — cuando el catálogo es más que un registro

**El caso que introduce la idea más avanzada del workshop: federación.**

### El problema

United procesa **miles de millones de eventos al día** en una plataforma que vive
a la vez en **Amazon Redshift y en Databricks con Unity Catalog**. Cura petabytes
en S3 con arquitectura *medallion*; solo la capa de interacción de usuario tiene
decenas de terabytes en streaming casi en tiempo real.

El problema no era almacenar. Era que los analistas necesitaban datos curados que
vivían en Databricks, desde herramientas que consultaban Redshift. La salida
obvia — copiar los datos — significa duplicar petabytes y mantener pipelines de
sincronización.

### Lo que construyeron

**Federación del AWS Glue Data Catalog.** En lugar de mover datos, el Glue Catalog
actúa como índice unificado: desde Redshift se consultan tablas Apache Iceberg
que viven en Databricks Unity Catalog.

```
                AWS Glue Data Catalog
                   (índice federado)
                  /               \
        Amazon Redshift      Databricks Unity Catalog
        (data warehouse)     (Iceberg sobre S3)
                  \               /
              consulta sin mover datos
```

### Resultados

| Métrica | Resultado |
|---------|-----------|
| Analistas con acceso en tiempo real a la capa *gold* en S3 | **~100** |
| Infraestructura de ETL redundante eliminada | **~$30.000/mes** |
| Fuentes de verdad | de dos plataformas desincronizadas a **una** |

### Conexión con el workshop

| Lo que hizo United | Nuestro lab |
|--------------------|-------------|
| Glue Data Catalog como índice común | **Lab 2** |
| Catalog Federation | Lab 2, mencionado: el concepto que extiende lo que construimos |
| Arquitectura medallion en S3 | Nuestras zonas `raw/` y `curated/` son la versión mínima |

**La lección:** en el Lab 2 el catálogo parece un registro de esquemas. United
muestra que es una capa de abstracción: **puedes consultar datos que no están
donde crees, sin moverlos.** Ese salto conceptual es lo que convierte un catálogo
en una plataforma.

**Fuentes:**
[How United Airlines uses Amazon Redshift and AWS Glue Data Catalog federation to query Databricks-managed data (AWS Big Data Blog)](https://aws.amazon.com/blogs/big-data/how-united-airlines-uses-amazon-redshift-and-aws-glue-data-catalog-federation-to-query-databricks-managed-data/) ·
[Access Databricks Unity Catalog data using catalog federation in the AWS Glue Data Catalog](https://aws.amazon.com/blogs/big-data/) ·
[Airlines Data Platform — AWS Reference Architecture](https://docs.aws.amazon.com/reference-architecture-diagrams/latest/airlines-data-platform/airlines-data-platform.html)

---

## Tabla de cierre: problema → patrón → lab → caso

La que se usa al final de la charla, cuando se revela que los tres labs eran una
arquitectura de referencia:

| Problema | Patrón arquitectónico | Lab | Caso real |
|----------|----------------------|-----|-----------|
| Datos en sistemas aislados, sin acceso | Ingesta centralizada con CDC | 1 | **AJE** (4–5 h de espera), BMW |
| Sin zona donde aterrizar los datos | Data lake, zona `raw/` | 1 | BMW (20 PB, 110 TB/día) |
| Datos crudos sin transformar | ETL `raw → curated` | 2 | AJE (−35% en ETL), BMW |
| Nadie sabe si los datos son fiables | Reglas de calidad medidas y publicadas | 2 | principio general |
| Nadie sabe qué significan los datos | Data Catalog con metadata de negocio | 2 | BMW, United |
| Datos duplicados entre plataformas | Catalog Federation | 2 (mención) | **United** (~$30K/mes ahorrados) |
| Control de acceso fino | Lake Formation | 2/3 (mención) | BMW |
| IA sin plataforma de datos debajo | RAG sobre datos curados | 3 | la evolución de los tres |

---

## Lo que tienen en común los tres

Ninguno empezó por la IA.

- **AJE** empezó porque sus equipos esperaban 5 horas por un dato.
- **BMW** empezó porque tenía datos en toda la organización sin una zona común.
- **United** empezó porque sus analistas no podían consultar datos que ya
  existían, solo porque vivían en otra plataforma.

Los tres tienen hoy IA encima: Bedrock en AJE y en BMW, búsqueda agéntica en BMW.
Pero llegó **después**, y sobre una plataforma que ya funcionaba.

> *"Una aplicación de IA no comienza con el modelo. Comienza con datos que pueden
> ser ingeridos, transformados, validados, entendidos, gobernados y finalmente
> entregados a la IA."*

Estos tres casos son la prueba de que eso no es una opinión.

---

## Nota sobre el uso de estos casos

- **Cita siempre la fuente en el slide.** Las cifras de esta página vienen de
  documentación oficial de AWS, y eso es lo que las hace utilizables.
- **Comprueba los enlaces antes de la charla.** AWS reorganiza su sitio y los
  case studies cambian de URL. Ten una captura de respaldo de cada cifra que
  cites.
- **Las cifras envejecen.** Los 20 PB de BMW son de la fecha de su publicación;
  hoy serán más. Si dices "más de 20 PB" estás a salvo.
