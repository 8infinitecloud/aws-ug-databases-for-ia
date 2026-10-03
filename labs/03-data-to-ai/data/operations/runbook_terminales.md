# Runbook de Operación de Terminales
## Versión 1.0 | Vigente desde: octubre 2026
## Área responsable: Operaciones de Adquirencia

---

## 1. Propósito

Un terminal caído es un comercio que no puede cobrar. Este runbook dice cómo
saber qué le pasa a un terminal y, sobre todo, cómo distinguir **un problema de
red del comercio** de **un problema con el emisor o un caso de fraude**. Los dos
se ven igual desde la base de datos —un cobro que no se completó— y se
resuelven de formas opuestas.

La base de datos dice **qué** pasó con el cobro. La telemetría del terminal dice
**por qué**.

---

## 2. De dónde sale la información

Cada terminal publica un **latido** cada 30 segundos en
`<device_serial>/telemetry`, por MQTT hacia AWS IoT Core. El cobro no va por ahí:
va por HTTPS a la API, como en cualquier terminal de pagos. La telemetría es
solo el estado del dispositivo.

| Dónde | Qué hay | Para qué |
|-------|---------|----------|
| Tabla `terminal-state` (DynamoDB) | El **último** latido de cada terminal, por `device_id` | Saber **ahora** qué terminal está caído |
| Tabla `telemetry` (Athena, sobre `raw/telemetry/`) | **Todos** los latidos, por día | La historia: cuándo empezó un problema y con qué señal |
| Tabla `transactions` (Athena, `curated/`) | Los cobros, con su `terminal_id` y `response_code` | Qué le pasó a cada cobro |

El `device_serial` es el serial del elemento seguro del terminal. Los terminales
simulados de la demo lo tienen con el prefijo `sim-`, y el terminal físico con el
serial del chip ATECC608.

---

## 3. Los campos del latido

| Campo | Qué mide | Normal | Investigar |
|-------|----------|--------|------------|
| `rssi_dbm` | Señal Wi-Fi o móvil | -40 a -65 | **por debajo de -75** |
| `battery_pct` | Batería | > 30 % | **por debajo de 15 %** |
| `http_timeouts` | Llamadas a la API que agotaron el plazo, desde el latido anterior | 0 | **cualquier valor sostenido** |
| `http_errors` | Respuestas 5xx o sin respuesta | 0 | más de 1 por latido |
| `http_latency_ms_avg` | Tiempo medio de respuesta de la API | < 800 ms | **por encima de 3000 ms** |
| `qr_shown`, `qr_paid` | QR mostrados y pagados | `qr_paid` cerca de `qr_shown` | conversión por debajo del 70 % |
| `qr_canceled`, `qr_expired` | QR que el cajero canceló o que caducaron sin pago | bajos | ver sección 6 |

Los contadores son **deltas**: lo ocurrido desde el latido anterior, no el total.
Para un periodo se suman.

### 3.1 Escala de señal (`rssi_dbm`)

| `rssi_dbm` | Calidad | Qué esperar |
|------------|---------|-------------|
| -40 a -60 | Excelente | Sin incidencias |
| -60 a -70 | Buena | Sin incidencias |
| -70 a -80 | Débil | Latencia alta, algún timeout |
| por debajo de -80 | **Crítica** | **Timeouts frecuentes: cobros que no se completan** |

---

## 4. ¿Terminal caído?

Un terminal está **offline** si lleva **más de 2 minutos sin latido**. Es la
misma regla con la que los procesadores de pago de referencia dan por
desconectado un lector.

1. Mirar en `terminal-state` el `received_at_ms` del terminal.
2. Si el último `battery_pct` era bajo y no estaba cargando (`charging = false`),
   lo más probable es que **se quedó sin batería**. Contactar al comercio para
   que lo conecte.
3. Si la batería estaba bien, revisar el `rssi_dbm` de los últimos latidos: una
   señal que se degradó antes de desaparecer es un problema de red del local.
4. Si el terminal no aparece en ninguna tabla, nunca latió: puede tener la
   telemetría desactivada o no estar provisionado. Escalar a Ingeniería.

Un terminal offline **no es un caso de fraude**. No se retiene la liquidación del
comercio por ello.

---

## 5. ¿Falló la red o el emisor?

Es la pregunta más frecuente, y la más fácil de responder mal.

| Lo que se ve | Diagnóstico | Acción |
|--------------|-------------|--------|
| Muchos `http_timeouts`, `rssi_dbm` por debajo de -75, y `response_code` normales en los cobros que sí llegan | **Red del comercio** | Contactar al comercio: revisar su Wi-Fi o la cobertura. **No es fraude** |
| Red normal, pero muchos cobros con `response_code = 05` (no honrar) | **Emisor** rechazando | Revisar el patrón con Monitoreo: puede ser card testing (ver *Política de Pagos Online*) |
| Red normal y muchos `response_code = 51` (fondos insuficientes) | Comportamiento del **tarjetahabiente** | Sin acción técnica |
| Red normal y muchos `response_code = 91` (emisor no disponible) | **Emisor o red de tarjetas** caídos | Escalar a Operaciones de Red; afecta a todos los comercios a la vez |
| `http_latency_ms_avg` alta en **todos** los terminales a la vez | **Nuestra API** | Escalar a Ingeniería: el problema no está en el comercio |

> **Un timeout no deja cobro.** Cuando la red falla esperando la respuesta, el
> terminal se rinde y la intención se queda sin cobrar (o se cancela con
> `cancellation_reason = abandoned`, según el terminal). En `transactions` ese
> intento **no aparece**: por eso un terminal con la red mal puede tener una
> tasa de rechazo normal y aun así estar perdiendo ventas. Solo la telemetría lo
> muestra.

La consulta guardada **«6. Falló la red o el emisor?»** de Athena cruza las dos
fuentes por `terminal_id`.

---

## 6. QR abandonados

El terminal distingue dos formas de que un QR no se pague, que la base de datos
no distingue:

- **`qr_canceled` alto**: el cajero cancela. Suele ser que el cliente prefirió
  pagar con tarjeta, o que el QR no se lee bien desde donde está el cliente.
  Revisar la colocación del terminal con el comercio.
- **`qr_expired` alto**: nadie canceló, el QR caducó esperando. El cliente se fue,
  o el pago desde el móvil no se completó. Si coincide con `rssi_dbm` bajo, el
  terminal no estaba recibiendo la confirmación: es un problema de red.

Una conversión (`qr_paid` / `qr_shown`) por debajo del 70 % durante una semana
merece una llamada al comercio.

---

## 7. Lo que este runbook no cubre

- Terminales con comportamiento de fraude (muchas operaciones seguidas, muchos
  rechazos): eso lo detecta el scoring en línea y lo trata Monitoreo, según el
  *Procedimiento Operativo de Monitoreo Transaccional*.
- Pagos online: no tienen terminal ni telemetría. Ver *Política de Pagos Online*.

---

## 8. Documentos relacionados

- Procedimiento Operativo de Monitoreo Transaccional — `response_code`, `terminal_id`
- Política de Pagos Online — card testing y 3-D Secure
