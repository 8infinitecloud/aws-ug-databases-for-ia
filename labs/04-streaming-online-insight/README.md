# Lab 4 — Streaming e insight online

Los tres primeros labs responden **qué pasó**. Este responde **si podemos
reaccionar ahora**, y esa no es la misma pregunta hecha más rápido: es otra
arquitectura.

| Lab | Pregunta | Velocidad | Dónde vive el dato |
|---|---|---|---|
| 1 | ¿cómo llegan los datos? | batch | S3 `raw/` |
| 2 | ¿son confiables? | batch | S3 `curated/` |
| 3 | ¿qué nos dicen? | consulta | pgvector + Bedrock |
| **4** | **¿podemos reaccionar ahora?** | **streaming** | **DynamoDB** |

El cambio de almacenamiento no es un detalle. S3 no es lento: **no es una base
de datos**. Para sumar uno a un contador y leerlo en el mismo milisegundo hace
falta otra cosa.

---

## Lo que se construye

```
  POS (Core2)
      │  MQTT
      ▼
  AWS IoT Core
      │
  IoT Rule  ──SELECT * FROM '+/events'
      │
      ├──────────────► Lambda ──► DynamoDB          ~milisegundos   REACCIONAR
      │                (scoring)   (contadores
      │                             + alertas)
      │
      └──────────────► Firehose ──► S3 raw/events/   ~60 segundos    RECORDAR
                                     │
                                     └──► Labs 1-2
```

Un evento, dos destinos, y la diferencia entre ellos **es el lab**.

> **Firehose no es el camino del insight online.** Su buffer mínimo son 60
> segundos, y con el Glue del Lab 2 por detrás la latencia real ronda los 4
> minutos. Eso es batch rápido. Aquí Firehose es la *toma* que alimenta el lago;
> quien reacciona es la Lambda.

---

## Las reglas: velocity checks

No son un ejemplo inventado. Son controles de fraude que un adquirente tiene de
verdad, y detectan lo que un comercio legítimo no produce.

| Regla | Qué mira | Por qué |
|---|---|---|
| `VELOCITY_COUNT` | más de 5 operaciones por terminal en 60 s | una cafetería no vende cada tres segundos |
| `DECLINE_RATIO` | más del 50 % rechazadas (mín. 4 muestras) | **la firma del card testing** |
| `VELOCITY_AMOUNT` | más de 500 acumulados en la ventana | monto inusual para la ventana |
| `HIGH_RISK_MCC` | importe alto en un comercio de riesgo alto | el MCC es una señal, no una etiqueta |

**`DECLINE_RATIO` es la interesante.** Quien prueba tarjetas robadas acumula
rechazos, porque la mayoría ya están bloqueadas. Por eso detecta el ataque antes
que la regla de volumen: el patrón está en los rechazos, no en la cantidad.

El mínimo de muestras no es un detalle: sin él, dos operaciones con un rechazo
dan el 50 % y marcaríamos a cualquiera con mala suerte temprana. Una regla que
marca operaciones normales acaba desactivada entera, y entonces no protege de
nada.

---

## Verlo sin AWS

Lo más útil antes de desplegar. Ejecuta **el mismo `lambda_handler`** contra un
doble de DynamoDB en memoria:

```bash
python scripts/run_streaming_local.py
```

```
── card-testing ──────────────────────────────────────────────────────
  Alguien prueba tarjetas robadas: ráfaga en el mismo minuto, casi todas rechazadas

  10:00:03  DEC     1.00     ok     1tx/   1.00
  10:00:06  DEC     1.00     ok     2tx/   2.00
  10:00:09  DEC     1.00     ok     3tx/   3.00
  10:00:12  DEC     1.00  MARCADA   4tx/   4.00  DECLINE_RATIO
  10:00:15  DEC     1.00  MARCADA   5tx/   5.00  DECLINE_RATIO
  10:00:18  DEC     1.00  MARCADA   6tx/   6.00  VELOCITY_COUNT, DECLINE_RATIO
```

Mira el orden: **salta el ratio en la cuarta, y el volumen sólo en la sexta.**

Calibrar es cambiar una variable y volver a ejecutar:

```bash
MAX_TX_PER_WINDOW=3 python scripts/run_streaming_local.py --escenario cafeteria
```

Las reglas son funciones puras y la E/S vive aparte. No es limpieza: es que una
regla de fraude se itera decenas de veces, y hacerlo contra un servicio
desplegado es inviable.

```bash
pytest tests/test_risk_scorer.py -v    # 21 tests, sin AWS
```

---

## Desplegar

Necesita el Lab 1 (el bucket) y `payments` (el backend que publica los eventos).

```bash
terraform -chdir=infra/lab4 init -backend-config=../backend.hcl
terraform -chdir=infra/lab4 apply
```

Los umbrales son variables y llegan a la Lambda por entorno, así que se ajustan
desde la consola antes de la charla sin redesplegar ni reflashear el POS:

```bash
terraform -chdir=infra/lab4 output thresholds
```

---

## Ver la demo

```bash
# El scoring, en vivo
aws logs tail "$(terraform -chdir=infra/lab4 output -raw scorer_log_group)" --follow

# Lo que quedó marcado
aws dynamodb query \
  --table-name "$(terraform -chdir=infra/lab4 output -raw velocity_table_name)" \
  --key-condition-expression "pk = :t AND begins_with(sk, :a)" \
  --expression-attribute-values '{":t":{"S":"T#TID00001"},":a":{"S":"ALERT#"}}'
```

Y un minuto después, los mismos eventos en el lago:

```bash
aws s3 ls "$(terraform -chdir=infra/lab4 output -raw events_s3_path)" --recursive
```

Ese minuto de espera **es el contenido**: enseña por qué existen dos caminos.

---

## Tres decisiones que no son obvias

**Los contadores van por terminal, no por comercio.** Un local con diez cajas
mueve diez veces más que uno con una. Agregando por comercio, el mismo umbral
significaría cosas distintas según el tamaño del local.

**El contador se incrementa con `ADD`, no leyendo y escribiendo.** Un
`UpdateItem` atómico no pierde eventos simultáneos. Leer-sumar-escribir —que es
lo que sale sin pensarlo— los perdería justo cuando llegan en ráfaga, que es
exactamente el caso que hay que detectar.

**La Lambda no devuelve error aunque falle.** Una excepción haría que IoT
reintentara, y reintentar un scoring ya contado inflaría los contadores y
dispararía alertas falsas. El cobro ya ocurrió; esto sólo lo observa.

---

## Ventanas fijas, no deslizantes

Una ventana deslizante exige guardar cada evento y recontar. Aquí basta un
contador por cubo de 60 segundos, que es una escritura y ninguna lectura.

La contrapartida es real: **un ataque a caballo entre dos cubos se reparte**. Se
asume a propósito. Para eso están las otras reglas, y una ventana deslizante
sobre DynamoDB costaría bastante más de lo que aporta a esta escala.

---

## Lo que este lab no hace

- **No bloquea el cobro.** Marca. Bloquear en línea exige que el scoring esté
  *dentro* del flujo de autorización, con su latencia en el camino crítico y un
  plan para cuando falle. Es otra conversación.
- **No detecta fraude.** Eso lo hace el emisor, que ve la tarjeta. Esto detecta
  *patrones* que un comercio legítimo no produce.
- **No aprende.** Son umbrales, no un modelo. Un modelo necesitaría etiquetas
  —qué resultó ser fraude— y esas llegan semanas después, con los contracargos.

---

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md)
