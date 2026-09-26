# Los frameworks de AWS detrás del workshop

El workshop no menciona estos frameworks mientras se ejecuta, pero cada decisión
sale de ellos. Esta página es la referencia silenciosa: **qué preguntas hace cada
framework, y dónde las responde el workshop.**

Están ordenados como se recorren: primero decides qué plataforma necesitas, luego
cómo construirla bien, y por último cómo poner IA encima sin romper nada.

---

## 1. AWS Well-Architected — Data Analytics Lens

**La pregunta que responde:** *¿estoy construyendo bien esta plataforma de datos?*

Es el Well-Architected Framework aplicado a analítica: los mismos seis pilares,
con **15 principios de diseño** específicos de plataformas de datos.

### Los 15 principios, por pilar

| Pilar | Principios |
|-------|-----------|
| **Excelencia operativa** | 1 — Monitorizar la salud del workload analítico<br>2 — Modernizar el despliegue de jobs y aplicaciones |
| **Seguridad** | 3 — Diseñar la plataforma para governance y compliance<br>4 — Implementar control de acceso a los datos<br>5 — Controlar el acceso a la infraestructura |
| **Fiabilidad** | 6 — Diseñar resiliencia para el workload analítico<br>7 — **Gobernar los cambios de datos y metadatos** |
| **Eficiencia del rendimiento** | 8 — Elegir la solución de cómputo con mejor rendimiento<br>9 — Elegir la solución de almacenamiento con mejor rendimiento<br>10 — **Elegir formato de fichero y particionado** |
| **Optimización de costes** | 11 — Cómputo y almacenamiento según el patrón de uso<br>12 — Modelos de responsabilidad financiera por dato y workload<br>13 — Gestionar el coste a lo largo del tiempo<br>14 — Usar el modelo de precios óptimo |
| **Sostenibilidad** | 15 — Guía de implementación sostenible |

### Dónde los responde el workshop

| Principio | Dónde | Cómo |
|-----------|-------|------|
| 1 — Monitorizar | Lab 2 | El quality report no es un log: es un artefacto versionado en S3 que dice el estado de los datos en cada ejecución |
| 2 — Modernizar despliegue | Los tres | Terraform con state remoto, y CI que valida antes de desplegar |
| 3 — Governance | Lab 2 | Glue Data Catalog con metadata de negocio: dueño, dominio, origen. Lake Formation se menciona como la extensión natural |
| 4 — Acceso a datos | Lab 2 | Rol de Glue acotado al bucket del lab. Lake Formation queda fuera del alcance a propósito |
| 5 — Acceso a infraestructura | Lab 1, 3 | Subnets privadas, acceso por security group y no por CIDR, despliegue por SSM sin SSH |
| 6 — Resiliencia | Lab 1, 3 | Dos AZ en los subnet groups; Local Zones excluidas del data source |
| 7 — **Cambios de metadatos** | Lab 2 | `SchemaChangePolicy` del Crawler: `UPDATE_IN_DATABASE` para cambios, `LOG` para borrados. Un esquema que cambia no debe romper el consumidor en silencio |
| 8 — Cómputo | Lab 2 | Glue Python Shell a 0.0625 DPU. Elegir Spark para 1000 filas sería *incumplir* este principio, no cumplirlo |
| 9 — Almacenamiento | Lab 1 | S3 con zonas por prefijo y ciclos de vida a IA |
| 10 — **Formato y particionado** | Lab 2 | Parquet columnar particionado por `year`/`month`, para que Athena lea solo lo que necesita |
| 11 — Coste según uso | Los tres | DynamoDB on-demand, Aurora Serverless v2, Glue por ejecución |
| 12 — Responsabilidad financiera | — | `default_tags` con `Project`, `Environment`, `Lab`: la base para repartir coste. Sin cost allocation real, por ser un lab |
| 13 — Coste en el tiempo | — | [COST_ESTIMATE.md](../COST_ESTIMATE.md) con el escenario de olvidar el cleanup y cómo poner un presupuesto |
| 14 — Modelo de precios | — | Solo on-demand. Savings Plans no aplican a algo que vive dos horas |
| 15 — Sostenibilidad | Los tres | Escalado a cero donde el servicio lo permite; Parquet comprimido; una sola región |

> **El principio 10 es el que más se subestima.** Elegir Parquet particionado en
> vez de CSV no es una optimización: cambia el coste de cada consulta que se haga
> sobre esos datos durante años. Se decide una vez y se paga (o se ahorra) para
> siempre.

**Fuentes:**
[Pilares del Data Analytics Lens](https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/well-architected-design-principles.html) ·
[Principios por pilar](https://docs.aws.amazon.com/wellarchitected/latest/analytics-lens/design-principles-by-pillar.html) ·
[Guía actualizada del Data Analytics Lens](https://aws.amazon.com/blogs/big-data/now-available-updated-guidance-on-the-data-analytics-lens-for-aws-well-architected-framework)

---

## 2. AWS Well-Architected — Generative AI Lens

**La pregunta que responde:** *¿estoy poniendo IA encima de esto de forma
responsable?*

Cubre los seis pilares a lo largo de **seis fases del ciclo de vida** de un
sistema de IA generativa, con **seis principios de diseño**.

### Los 6 principios de diseño

| Principio | Qué pide | Dónde lo toca el workshop |
|-----------|----------|---------------------------|
| **Design for controlled autonomy**<br>*Diseñar para autonomía controlada* | Guardarraíles y límites que gobiernen cómo el sistema opera, escala e interactúa; condiciones de fallo explícitas | Lab 3: el RAG solo responde desde el corpus recuperado, con `RETRIEVAL_MIN_SCORE = 0.70`. Si nada supera el umbral, dice que no sabe en vez de inventar |
| **Implement comprehensive observability**<br>*Observabilidad completa* | Medir desde seguridad y rendimiento hasta coste e impacto ambiental, en cada capa | Lab 3: la UI muestra los chunks recuperados con su score. El usuario ve **por qué** el modelo respondió eso |
| **Optimize resource efficiency**<br>*Eficiencia de recursos* | Elegir y configurar según requisitos empíricos, no suposiciones. Dimensionar el modelo correctamente | Lab 3: Nova Pro en vez de Claude para un lab repetido. La decisión es de coste medido, no de prestigio del modelo |
| **Establish distributed resilience**<br>*Resiliencia distribuida* | Seguir operando ante fallos de componente o de región | Lab 3: si Redis cae, el RAG sigue sin memoria de sesión. Reintentos ante throttling de Bedrock |
| **Standardize resource management**<br>*Gestión estandarizada de recursos* | Catálogos y controles centralizados de prompts, modelos y permisos; control de versiones | Lab 3: prompts en `query/prompt_builder.py`, no dispersos. Modelos en `ingestion/config.py`. El rol del EC2 acotado a los dos modelos permitidos |
| **Secure interaction boundaries**<br>*Fronteras de interacción seguras* | Mínimo privilegio, comunicaciones seguras, saneamiento de entrada y salida, monitorización | Parcial: IAM de mínimo privilegio e IMDSv2, pero **Streamlit está abierto al mundo** y no hay saneamiento de entrada. Concesión de lab, documentada en [well-architected.md](well-architected.md) |

> **Lo que este lens deja claro:** los pilares de la IA generativa **no son
> nuevos**. Son los mismos seis, aplicados a un componente que además es
> no-determinista y cobra por token. Por eso el Lab 3 es el más corto del
> workshop: si los Labs 1 y 2 están bien, el 3 es poner un modelo encima.

**Fuentes:**
[Generative AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/generative-ai-lens.html) ·
[Principios de diseño](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/design-principles.html) ·
[Anuncio del lens](https://aws.amazon.com/blogs/architecture/announcing-the-aws-well-architected-generative-ai-lens)

---

## 3. El marco de estrategia de datos

**La pregunta que responde:** *¿qué plataforma necesito, y en qué orden la
construyo?*

Los dos lenses anteriores responden *cómo* construir bien. Este responde *qué*
construir, y es el que da forma a la progresión de los tres labs.

La secuencia, que es la del workshop:

```
1. INGESTA      ¿Cómo entran los datos, sin tocar el origen?          → Lab 1
2. ALMACÉN      ¿Dónde aterrizan antes de tocarlos?                   → Lab 1
3. TRANSFORM.   ¿Cómo se convierten en algo usable?                   → Lab 2
4. CALIDAD      ¿Puedo confiar en ellos, y cómo lo demuestro?         → Lab 2
5. CATÁLOGO     ¿Alguien sabe qué significan?                         → Lab 2
6. GOVERNANCE   ¿Quién puede ver qué?                                 → mención
7. CONSUMO      ¿Analítica, ML, IA?                                   → Lab 3
```

### Por qué el orden importa

**Cada etapa depende de la anterior, y saltarse una no se nota hasta el final.**

- Sin ingesta fiable, transformas datos incompletos.
- Sin zona `raw/`, no puedes reprocesar cuando descubres que la transformación
  estaba mal.
- Sin reglas de calidad, la IA responde con seguridad sobre datos rotos — que es
  el peor fallo posible, porque es invisible.
- Sin catálogo, cada equipo reinventa qué significa cada columna, y llegan a
  conclusiones distintas.

La mayoría de los tutoriales de RAG empiezan en la etapa 7 con documentos ya
limpios en una carpeta. **Este workshop empieza en la 1**, y esa es toda su tesis.

### Las preguntas que hay que saber responder

Antes de poner IA sobre unos datos, en este orden:

1. ¿De dónde vienen estos datos, y cuándo se actualizaron por última vez?
2. ¿Qué transformaciones se les aplicaron, y quién decidió esas reglas?
3. ¿Qué registros se descartaron, y dónde están ahora?
4. ¿Quién es el dueño de este dato?
5. ¿Quién puede verlo?
6. Si el modelo responde algo incorrecto, ¿puedo rastrear de qué dato salió?

El workshop responde 1, 2, 3 y 6 con lo que construye:
DMS con CDC, `transform_stats`, la zona `rejected/` con `rejection_reason`, y los
chunks con score visibles en la UI. Las preguntas 4 y 5 se responden con metadata
en el catálogo y con Lake Formation, que se menciona.

> **La pregunta 6 es la que justifica todo lo demás.** Un asistente de IA que no
> puede citar su fuente no es una herramienta de compliance: es un generador de
> afirmaciones plausibles. Y para citar la fuente hacen falta las seis etapas
> anteriores.

---

## Cómo usar esto en la charla

**No los menciones por nombre.** Nadie se emociona con un framework a los tres
minutos de una charla de 30. Úsalos así:

- **Como orden narrativo.** La progresión de los tres labs *es* el marco de
  estrategia de datos. El público lo recorre sin que se lo nombres.
- **Como respaldo cuando pregunten.** «¿Por qué Parquet particionado?» tiene una
  respuesta mejor que "es más rápido": es el principio 10 del Data Analytics Lens,
  y cambia el coste de cada consulta durante años.
- **Como cierre.** Al final, cuando se revela que los tres labs eran una
  arquitectura de referencia, decir que eso sale de los lenses de AWS convierte
  el workshop en algo reproducible en el trabajo del lunes.

Para la revisión concreta de este workshop contra los seis pilares, con lo que
cumple y lo que concede a propósito: [docs/well-architected.md](well-architected.md).
