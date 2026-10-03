# Lab 4 — Streaming e insight online

> **Lab 4 de 4** · [← Lab 3](../03-data-to-ai/README.md) · [Índice](../../README.md)

| | |
|---|---|
| **Tiempo** | ~20 min |
| **Coste** | ~$0 en reposo: todo es por uso |
| **Qué despliegas** | Reglas de IoT Core, Lambda de scoring, DynamoDB, Firehose |
| **Requisitos** | El core (`infra/payments`) y el Lab 1 (su bucket) desplegados |

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

Por IoT Core pasan dos cosas distintas, y cada una tiene su camino:

```
  backend de pagos ── <serial>/events ──┐   (al cobrar; online/events sin terminal)
                                         ▼
                                    AWS IoT Core
                                         │
  IoT Rule  ──'+/events'  ───────────────┼──► Lambda ──► DynamoDB      ~ms    REACCIONAR
                                         │    (scoring)   (contadores
                                         │                 + alertas)
  terminal ── <serial>/telemetry ────────┘
                                         │
  IoT Rule  ──'+/telemetry' ─────────────┼──► Firehose ──► S3 raw/telemetry/   ~60 s   RECORDAR
                                         └──► DynamoDB terminal-state           ~ms    VIGILAR
```

**Los pagos ya no van al lago por aquí.** Llegan por el CDC del Lab 1, que además
trae lo que el evento no tiene: el cobro que cambia de estado, la devolución, el
libro mayor. Mandarlos también por Firehose era tenerlos dos veces en `raw/` con
dos formas distintas.

**Firehose lleva ahora la telemetría de los terminales**: el latido, la batería,
la señal, la latencia de cada llamada a la API, los QR abandonados. Ese dato no
está en ninguna base: si no entra por aquí, no entra. Es lo que permite preguntar
**por qué** falló un cobro, y no solo que falló (ver la consulta 6 de Athena en el
Lab 2 y el *Runbook de Operación de Terminales* del Lab 3).

> **Firehose no es el camino del insight online.** Su buffer mínimo son 60
> segundos. Por eso el último latido de cada terminal se escribe también, sin
> Lambda, en la tabla `terminal-state`: es lo que responde **ahora** qué terminal
> lleva más de 2 minutos sin dar señales, la regla con la que se da por
> desconectado un lector de pagos.

---

## Las reglas: velocity checks

No son un ejemplo inventado. Son controles de fraude que un adquirente tiene de
verdad, y detectan lo que un comercio legítimo no produce.

| Regla | Qué mira | Por qué |
|---|---|---|
| `VELOCITY_COUNT` | más de 5 operaciones por terminal en 60 s (30 por tienda online) | una cafetería no vende cada tres segundos |
| `DECLINE_RATIO` | más del 50 % rechazadas (mín. 4 muestras) | **la firma del card testing** |
| `VELOCITY_AMOUNT` | más de 500 acumulados en la ventana (20 000 por tienda online) | monto inusual para la ventana |
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
pytest tests/test_risk_scorer.py -v    # sin AWS
```

---

## Paso 1: Desplegar

```bash
./labs/04-streaming-online-insight/scripts/setup.sh
```

Crea las reglas de IoT (`+/events` y `+/telemetry`), la Lambda de scoring, las
tablas de velocidad y de estado de la flota, y el Firehose que escribe en
`raw/telemetry/` del bucket del Lab 1.

Los umbrales son variables de Terraform y llegan a la Lambda por entorno: se
ajustan en la consola de Lambda sin redesplegar ni regrabar el terminal.

```bash
terraform -chdir=infra/lab4 output thresholds
```

## Paso 2: Validar

```bash
./labs/04-streaming-online-insight/scripts/validate.sh
```

Resultado esperado:

```
[✓] Tabla fincorp-datalake-lab-velocity activa
[✓] Lambda fincorp-datalake-lab-scorer desplegada (concurrencia reservada: 10)
[✓] Firehose fincorp-datalake-lab-telemetry activo
[✓] La Lambda marco la operacion: {"flagged": true, "rules": ["HIGH_RISK_MCC"]}
[✓] La alerta quedo en DynamoDB (1 encontrada/s)
[✓] Una venta corriente no se marca
[✓] El latido llego a fincorp-datalake-lab-terminal-state por la regla de IoT
[✓] Lab 4 validado
```

## Paso 3: El scoring

En una terminal aparte, el log de la Lambda:

```bash
aws logs tail "$(terraform -chdir=infra/lab4 output -raw scorer_log_group)" --follow
```

Haz un pago (tienda, terminal Core2 o generador). Cada cobro aparece en menos de
un segundo:

```
evento: {"event_type": "payment.captured", "pos_id": "TID00001", "amount": 26.5, ...}
ok ch_PIADCB14FB_1 | 1 tx / 26.50 en la ventana
```

Un ataque de card testing contra la tienda online PixelPlay (`MID000000020010`)
por la API real; queda también en la base, en el CDC y en el lago:

```bash
python scripts/generate_traffic.py \
  --api "$(terraform -chdir=infra/payments output -raw api_endpoint)" --card-testing
```

Resultado esperado: a partir del cuarto intento el log muestra `MARCADA` con
`DECLINE_RATIO`. Lo marcado queda en DynamoDB:

```bash
aws dynamodb query \
  --table-name "$(terraform -chdir=infra/lab4 output -raw velocity_table_name)" \
  --key-condition-expression "pk = :t AND begins_with(sk, :a)" \
  --expression-attribute-values '{":t":{"S":"M#MID000000020010"},":a":{"S":"ALERT#"}}'
```

Las tiendas online van por comercio (`M#<merchant_id>`); los terminales, por
terminal (`T#<terminal_id>`).

Sin la API desplegada, `./labs/04-streaming-online-insight/scripts/run_demo.sh
card-testing` publica los eventos directamente en IoT Core.

## Paso 4: La telemetría

**La flota virtual** (nueve terminales, con un taxi de mala cobertura y otro
que se queda sin batería):

```bash
API="$(terraform -chdir=infra/payments output -raw api_endpoint)"
python scripts/generate_traffic.py --api "$API" --duration 900 --telemetria
```

**El terminal Core2**: activa la telemetría en su firmware y grábalo, ver
[payments/firmware/README.md](../../payments/firmware/README.md#telemetría-opcional-desactivada-por-defecto).
Publica un latido cada 30 s en `<serial>/telemetry`.

El estado de la flota, al instante:

```bash
aws dynamodb scan --table-name "$(terraform -chdir=infra/lab4 output -raw terminal_state_table)" \
  --projection-expression "device_id, terminal_id, battery_pct, rssi_dbm, received_at_ms"
```

La misma telemetría en el lago, ~60 s después (el buffer mínimo de Firehose):

```bash
aws s3 ls "$(terraform -chdir=infra/lab4 output -raw telemetry_s3_path)" --recursive
```

Las carpetas `year=/month=/day=` de Firehose van en UTC.

## Paso 5: La telemetría en Athena y en el RAG

1. Ejecuta el job y el crawler del Lab 2 para registrar la tabla `telemetry`:

   ```bash
   ./labs/02-transformation-quality-catalog/scripts/run_job.sh
   ```

2. En Athena (workgroup `fincorp-datalake-lab`), ejecuta las consultas guardadas
   6, 7 y 8: terminales caídos, red o emisor, abandono del QR.
3. Con el Lab 3 desplegado, actualiza los hechos y pregunta al asistente:

   ```bash
   ./scripts/demo_cierre.sh
   ```

   ```
   ¿Qué terminales están caídos o con batería baja?
   Un terminal tiene muchos http_timeouts: ¿falló la red o el emisor?
   ```

---

## Cleanup

```bash
./labs/04-streaming-online-insight/scripts/cleanup.sh
```

Destruye las reglas, la Lambda, Firehose y las tablas. La telemetría escrita en
`raw/telemetry/` es del bucket del Lab 1 y se borra con su cleanup. Hazlo
**antes** del cleanup del Lab 1.

---

## Tres decisiones que no son obvias

**Los contadores van por terminal, no por comercio.** Un local con diez cajas
mueve diez veces más que uno con una. Agregando por comercio, el mismo umbral
significaría cosas distintas según el tamaño del local. La excepción es la
tienda online, que no tiene terminal: va por comercio (`M#` en vez de `T#`) y con
sus propios umbrales, porque suma a todos sus compradores a la vez. El ratio de
rechazos no se relaja: el card testing online es justo su caso.

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
  plan para cuando falle.
- **No detecta fraude.** Eso lo hace el emisor, que ve la tarjeta. Esto detecta
  *patrones* que un comercio legítimo no produce.
- **No aprende.** Son umbrales, no un modelo. Un modelo necesitaría etiquetas
  —qué resultó ser fraude— y esas llegan semanas después, con los contracargos.

---

¿Algo no cuadra? → [docs/troubleshooting.md](../../docs/troubleshooting.md)

| [← Lab 3](../03-data-to-ai/README.md) | [Índice](../../README.md) | [Costes](../../COST_ESTIMATE.md) |
|---|---|---|
