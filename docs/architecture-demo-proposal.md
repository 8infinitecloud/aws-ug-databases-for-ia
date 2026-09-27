# Architecture & Demo Proposal

> **Estado: propuesta. Nada de esto está implementado.**
>
> Este documento describe componentes que se están evaluando. Lo que sí existe
> está en [docs/architecture.md](architecture.md) y en los tres labs.
> No confundir una propuesta con el estado del repositorio.

---

# Smart Coffee Physical Demo

Un POS físico sobre M5Stack Core2 que genera eventos de pago reales durante la
charla, para demostrar que una plataforma de datos empieza cuando ocurre un
evento de negocio — no en S3.

```
COFFEE SALE → DATA → STREAM → QUALITY → CONTEXT → CURATED → ANALYTICS → AI
```

**Nada de esto procesa pagos reales.** Ver [Consideraciones de seguridad](#9-consideraciones-de-seguridad).

---

## 1. Investigación previa

Antes de proponer arquitectura, lo que verifiqué sobre el hardware y los
servicios.

### 1.1 M5Stack Core2 — capacidades reales

| Característica | Valor | Relevancia para el POS |
|---|---|---|
| MCU | ESP32 D0WDQ6-V3, dos núcleos Xtensa LX6 a 240 MHz | Sobra para una UI de POS y un cliente MQTT |
| Memoria | 16 MB Flash, 8 MB PSRAM | Suficiente para TLS mutuo, que es lo que más consume |
| Pantalla | 2.0" capacitiva táctil | Selección de productos por toque, sin botones físicos |
| Botones | 3 táctiles capacitivos programables bajo la pantalla | Atajos: confirmar, cancelar, volver |
| Conectividad | Wi-Fi y Bluetooth | Wi-Fi es la vía a AWS |
| RTC | Integrado | **Importante:** da `timestamp` fiable sin depender de NTP |
| Otros | IMU 6 ejes, micrófono, altavoz, motor de vibración, ranura SD, batería 390 mAh | Vibración y sonido al aprobar el pago: buen efecto en vivo |
| Alimentación | USB-C, gestión por chip AXP192 | La batería de 390 mAh es pequeña: **mantenerlo enchufado durante la charla** |

> **Atención al comprar.** Las versiones **v1.0 y v1.1 están EOL** (discontinuadas).
> La vigente es la **v1.3**. Verificar antes de pedirlo.

### 1.2 Generación de QR — resuelto sin hardware extra

La librería `M5Core2` incluye `M5.Lcd.qrcode()` de serie:

```cpp
M5.Lcd.qrcode("https://pay.smartcoffee.demo/t/TX10023", 0, 0, 150, 6);
//             contenido                                  x  y  ancho  versión
```

No hace falta el módulo *M5Module-QRCode*: ese es un **lector**, y aquí sólo
necesitamos **mostrar**.

**Decisión de diseño que se deriva de esto:** el QR debe contener **una URL
corta**, no el JSON de la transacción. Una versión 6 admite ~134 caracteres
alfanuméricos; el JSON del §24 de la especificación ocupa más, obligaría a una
versión alta, y un QR denso en una pantalla de 2 pulgadas **no se escanea con un
móvil desde el público**. La web recupera los detalles con el `transaction_id`
que viene en la URL.

### 1.3 Conectividad con AWS IoT Core — viable

MQTT sobre TLS mutuo con certificado X.509 es el camino estándar y está bien
soportado en ESP32:

- `WiFiClientSecure` viene en el core de Arduino para ESP32
- `PubSubClient` para MQTT, `ArduinoJson` para el payload
- Puerto 8883, con Root CA de Amazon + certificado de dispositivo + clave privada
  compilados en el firmware

**Veredicto: es viable y no es "excesivamente complejo"** como temía el §28. Son
tres ficheros PEM en un header y unas 20 líneas de setup. Se hace una vez.

---

## 2. Arquitectura

```
                        SMART COFFEE
                              │
                ┌─────────────┴─────────────┐
                │                           │
        M5Stack Core2 POS            QR Payment Web
        (CARD: CVV en pantalla)      (S3 static website)
                │                           │
                │                    ┌──────┘
                │                    │
                │              API Gateway (HTTP API)
                │                    │
                │              Lambda: payment-mock
                │              APPROVED / DECLINED
                │                    │
                └─────────┬──────────┘
                          │
                   PAYMENT EVENT (normalizado)
                          │
                          ▼
                   AWS IoT Core
                   topic: smartcoffee/pos/+/events
                          │
                   IoT Rule (SQL)
                          ▼
                 Amazon Data Firehose
                 (buffer 60 s → S3)
                          │
                          ▼
                       S3 RAW
                  raw/events/YYYY/MM/DD/
                          │
                          ▼
                    AWS Glue  (Lab 2, ya existe)
                          │
                    Data Quality
                      /         \
                 VALID           INVALID
                    │               │
                    ▼               ▼
              CURATED          QUARANTINE
                    │           (rejected/)
           ┌────────┴────────┐
           ▼                 ▼
       Analytics            AI
       (Athena)         Coffee Assistant
                              │
                              ▼
                             RAG  (Lab 3, ya existe)
```

### 2.1 Desviación propuesta: Firehose en lugar de Kinesis Data Streams

El §26 de la especificación pide **Kinesis Data Streams**. Propongo **Amazon Data
Firehose**, y estas son las razones medidas:

| | Kinesis Data Streams | Amazon Data Firehose |
|---|---|---|
| Coste por hora | On-demand: **$0.040/h por stream**. Provisionado: $0.015/shard-hora | **$0** para entrega estándar a S3 |
| Coste por datos | $0.08/GB entrada (on-demand) | Por GB ingerido, en incrementos de 5 KB |
| Nivel gratuito | **No tiene** | No tiene, pero sin cargo horario el coste de la demo es ~$0 |
| Escritura a S3 | **Hay que escribir un consumidor** (Lambda + checkpointing) | Automática, con buffering |
| Piezas que mantener | Stream + consumidor + permisos | Stream de entrega |

Con ~50 eventos de ~300 bytes en una demo de 2 horas: Firehose factura 50 × 5 KB
= 250 KB, prácticamente nada. Kinesis Data Streams cobraría $0.08 sólo por
existir dos horas, **más** el Lambda consumidor que hay que escribir y depurar.

AWS IoT Core tiene acción de regla nativa para Firehose, así que el camino es
igual de directo.

**El contrapeso, y es real:** Firehose bufferiza con un **mínimo de 60 segundos**
antes de escribir a S3. En la demo en vivo eso significa que el evento **no
aparece en `raw/` al instante**. Hay que planificarlo (ver §11) o aceptar KDS +
Lambda por ~$0.10 y más complejidad.

> **Recomendación:** Firehose. El concepto que enseña la charla es *streaming
> ingestion*, no un producto concreto, y el §34 dice «make the important parts
> real»: lo importante es que el evento físico llegue a la plataforma, no qué
> servicio lo transporta. Si en la charla quieres decir «Kinesis», Firehose
> **es** parte de la familia Kinesis.

---

## 3. Componentes

| Componente | Tecnología | Real o mock | Nuevo |
|---|---|---|---|
| POS físico | M5Stack Core2 v1.3 + Arduino/PlatformIO | **Real** | Sí |
| UI del POS | `M5Core2.h`, pantalla táctil | **Real** | Sí |
| Generación de QR | `M5.Lcd.qrcode()` | **Real** | Sí |
| Web de pago QR | HTML/CSS/JS estático en S3 | **Real** | Sí |
| Procesador de pagos | Lambda `payment-mock` | **Mock** | Sí |
| Autorización de tarjeta | Lambda, validación de CVV de prueba | **Mock** | Sí |
| API | API Gateway HTTP API | **Real** | Sí |
| Transporte de eventos | AWS IoT Core, MQTT + TLS mutuo | **Real** | Sí |
| Entrega a S3 | Amazon Data Firehose | **Real** | Sí |
| Data lake | S3, zona `raw/events/` | **Real** | Reutiliza Lab 1 |
| Transformación y calidad | AWS Glue | **Real** | Reutiliza Lab 2 |
| Catálogo | Glue Data Catalog | **Real** | Reutiliza Lab 2 |
| Asistente | Bedrock + pgvector | **Real** | Reutiliza Lab 3 |

**Lo que se reutiliza es la mitad.** El bucket, el pipeline de Glue, las reglas
de calidad, el catálogo y el RAG ya existen. Lo nuevo es la fuente de eventos.

---

## 4. Flujo de pago

### 4.1 Tarjeta (simulada)

```
1. El usuario selecciona productos en el POS y toca [ 💳 TARJETA ]
2. El POS muestra la tarjeta ficticia preconfigurada: VISA **** **** 4242
3. El usuario introduce un CVV de prueba en el teclado táctil
4. POS → HTTPS POST /payments  { card_token: "demo_card_4242", cvv, amount }
5. Lambda payment-mock valida el CVV contra el valor esperado
6. Respuesta: APPROVED (con authorization_code) o DECLINED
7. El POS muestra el resultado, vibra y emite un sonido
8. El POS publica PAYMENT_COMPLETED o PAYMENT_FAILED por MQTT
```

El CVV correcto se guarda como variable de entorno de la Lambda, no en el
firmware. Así se puede cambiar en la consola antes de la charla sin reflashear.

### 4.2 QR (simulado)

```
1. El usuario selecciona productos y toca [ ▣ QR ]
2. El POS publica ORDER_CREATED y muestra el QR con la URL de pago
3. Alguien del público escanea el QR con su móvil
4. Se abre la web estática en S3: importe, transaction_id y botón [ PAGAR ]
5. La web llama a API Gateway → Lambda payment-mock
6. La Lambda responde PAID o DECLINED
7. La web muestra ✓ PAYMENT APPROVED
8. La Lambda publica PAYMENT_COMPLETED en IoT Core
9. El POS, suscrito al topic de resultados, actualiza su pantalla
```

**Detalle que hace que esto funcione en vivo:** el paso 9 requiere que el POS
esté **suscrito** a un topic, no sólo publicando. Es el que cierra el círculo y
el que hace que el público vea la pantalla del POS cambiar cuando alguien paga
desde su móvil. Merece la pena implementarlo bien.

### 4.3 Quién publica el evento

| Canal | Quién publica en IoT Core | Por qué |
|---|---|---|
| Tarjeta | El **POS**, tras recibir la respuesta | El POS conoce el resultado y ya tiene conexión MQTT |
| QR | La **Lambda** | El pago se confirma desde el móvil; el POS puede no estar mirando |

Esa asimetría es deliberada y no rompe nada, porque **el evento es el mismo**
(§5). Es además un buen punto didáctico: en una arquitectura de eventos importa
el contrato, no quién lo emite.

---

## 5. Esquema de eventos

Un único esquema para los dos canales. Es lo que pide el §26 y es lo que hace
que el pipeline no tenga que saber de dónde vino el pago.

```json
{
  "event_type": "PAYMENT_COMPLETED",
  "event_id": "evt_01JQ8Z3K5M",
  "event_version": "1.0",
  "transaction_id": "TX-10023",
  "store_id": "SJM-001",
  "pos_id": "POS-01",
  "payment_method": "QR",
  "amount": 18.50,
  "currency": "PEN",
  "items": [
    { "product_id": "P001", "product": "Americano",  "quantity": 1, "unit_price": 8.50 },
    { "product_id": "P004", "product": "Croissant",  "quantity": 1, "unit_price": 7.00 }
  ],
  "authorization_code": "MOCK-7821",
  "card_token": "demo_card_4242",
  "timestamp": "2026-09-27T21:50:00Z"
}
```

### 5.1 Campos, y por qué cada uno

| Campo | Tipo | Notas |
|---|---|---|
| `event_type` | enum | `ORDER_CREATED`, `PAYMENT_COMPLETED`, `PAYMENT_FAILED` |
| `event_id` | string | Único por evento. **Distinto de `transaction_id`**: una transacción genera varios eventos |
| `event_version` | string | Versionar el contrato desde el día uno cuesta nada y evita un problema después |
| `transaction_id` | string | La clave de negocio. Es la que se duplica a propósito para el Lab 2 |
| `store_id`, `pos_id` | string | Dimensiones para agregación. `pos_id` permite varios dispositivos |
| `payment_method` | enum | `CARD` \| `QR`. Lo único que distingue los dos canales |
| `amount`, `currency` | number, enum | `PEN` por defecto. **Coincide con el dataset de los Labs 1-2**, que ya usa `PEN` |
| `items[]` | array | Permite analítica por producto. Es lo que hace interesante el Athena del final |
| `authorization_code` | string | `MOCK-` como prefijo, para que sea evidente en los datos que es simulado |
| `card_token` | string | Sólo si `payment_method = CARD`. **Token ficticio, nunca un PAN** |
| `timestamp` | ISO 8601 UTC | Del RTC del Core2. Ver §9.4 sobre la deriva del reloj |

### 5.2 Eventos priorizados

El §27 lista cinco. Propongo implementar tres, en este orden:

1. **`PAYMENT_COMPLETED`** — el evento principal de la demo
2. **`ORDER_CREATED`** — necesario para el flujo QR (el pedido existe antes del pago)
3. **`PAYMENT_FAILED`** — el que demuestra el camino del CVV incorrecto

`PAYMENT_PENDING` y `ORDER_CANCELLED` quedan fuera: añaden estados sin añadir
lección.

---

## 6. Integración del M5Stack

### 6.1 Topics MQTT

```
smartcoffee/pos/{pos_id}/events      ← el POS publica  (QoS 1)
smartcoffee/pos/{pos_id}/results     ← el POS se suscribe (QoS 1)
```

La política de IoT debe acotarse a esos dos topics con el `pos_id` del
certificado, no con `*`.

### 6.2 Estructura del firmware

```
firmware/smart-coffee-pos/
├── platformio.ini          # PlatformIO: más reproducible que el IDE de Arduino
├── src/
│   ├── main.cpp            # setup/loop, máquina de estados
│   ├── ui.cpp              # pantallas: catálogo, total, pago, resultado
│   ├── catalog.cpp         # productos y precios (en firmware: no hay inventario)
│   ├── payment.cpp         # HTTPS al mock de tarjeta
│   ├── mqtt.cpp            # IoT Core: publicar y suscribirse
│   └── secrets.h.example   # plantilla; secrets.h va en .gitignore
└── README.md
```

> **`secrets.h` nunca se commitea.** Contiene la clave privada del dispositivo.
> Se añade a `.gitignore` junto con `*.pem` — que ya está cubierto por las reglas
> actuales del repositorio.

### 6.3 Máquina de estados

```
IDLE ──toque──▶ CATALOG ──añadir──▶ CART ──[PAGAR]──▶ METHOD
                   ▲                  │                 │
                   └──────quitar──────┘         ┌───────┴───────┐
                                                ▼               ▼
                                          CARD_CVV          QR_SHOWN
                                                │               │
                                                ▼               │
                                          AUTHORIZING           │ (espera MQTT)
                                                │               │
                                                └───────┬───────┘
                                                        ▼
                                                APPROVED / DECLINED
                                                        │
                                                   (3 s, vibra)
                                                        ▼
                                                      IDLE
```

`AUTHORIZING` tiene que ser un estado visible: da feedback mientras la petición
viaja, y sin él la pantalla parece congelada.

---

## 7. Diseño de la API

### 7.1 `POST /payments` — mock de tarjeta

```json
// petición
{ "transaction_id": "TX-10023", "card_token": "demo_card_4242",
  "cvv": "123", "amount": 18.50, "currency": "PEN",
  "store_id": "SJM-001", "pos_id": "POS-01" }

// 200 aprobado
{ "transaction_id": "TX-10023", "status": "APPROVED",
  "authorization_code": "MOCK-7821", "payment_method": "CARD" }

// 200 rechazado  (200, no 4xx: un rechazo es una respuesta de negocio válida)
{ "transaction_id": "TX-10023", "status": "DECLINED",
  "decline_reason": "INVALID_CVV", "payment_method": "CARD" }
```

### 7.2 `GET /transactions/{id}` — la web consulta el pedido

Devuelve importe, items y estado. Es lo que permite que el QR lleve sólo una URL
corta.

### 7.3 `POST /transactions/{id}/confirm` — mock de QR

Lo llama la web al pulsar [PAGAR]. Devuelve `PAID` o `DECLINED`, y **publica el
evento en IoT Core**.

### 7.4 Notas de diseño

- **HTTP API**, no REST API: ~70 % más barata y sobra para esto
- **Sin autenticación en la API.** Es un mock de demo sin datos reales. Se
  documenta explícitamente, y se limita con throttling para que no se pueda abusar
- **Idempotencia por `transaction_id`**: confirmar dos veces el mismo pedido no
  genera dos eventos. En la demo pasará, porque alguien pulsará dos veces

---

## 8. Calidad de datos con Smart Coffee

El §31 pide generar eventos inválidos controladamente. Propongo un **modo demo**
activable desde el POS con los tres botones táctiles, que inyecta un defecto en
el siguiente evento.

| Defecto | Cómo se inyecta | Regla del Lab 2 que lo detecta |
|---|---|---|
| `quantity` nula | Se omite el campo | `IsComplete "quantity"` |
| `quantity` negativa | `-2` | `ColumnValues "quantity" > 0` |
| `amount` nulo | Se omite | `Completeness "amount"` — ya existe |
| `amount` negativo | Signo invertido | `ColumnValues "amount" > 0` |
| `transaction_id` duplicado | Reenvía el id anterior | `Uniqueness` — ya existe |
| `payment_method` inválido | `"EFECTIVO"` | `ColumnValues "payment_method" in [CARD, QR]` |
| `timestamp` ausente | Se omite | `IsComplete "timestamp"` |

**Esto encaja bien con lo que ya existe:** tres de las siete reglas ya están en el
ruleset del Lab 2. Las otras cuatro se añaden al mismo DQDL, y hay que
actualizar `quality_rules.dqdl`, `glue_job.py` y los tests, que fijan los
números documentados.

> **Cuidado con la coherencia.** El Lab 2 mide hoy 8 reglas en tres etapas y los
> tests fijan «100 filas rechazadas». Un dataset de eventos nuevo cambia esos
> números. Hay que decidir si el pipeline de eventos es **un segundo pipeline**
> (más limpio: `raw/events/` → `curated/events/`) o si se fusiona con el de
> transacciones. **Recomiendo segundo pipeline**, para no romper la calibración
> que ya está probada.

---

## 9. Consideraciones de seguridad

### 9.1 Lo que este sistema NO hace

Debe quedar escrito en el README del componente y en el propio POS:

```
DEMO EDUCATIVA — NO PROCESA PAGOS REALES
No hay integración con Visa, Mastercard, Yape, Plin ni ningún
procesador. No se transmiten ni almacenan datos de tarjetas reales.
```

- **Nunca un PAN real**, ni de prueba de una pasarela real. Sólo `demo_card_4242`
- **Nunca un CVV real.** El del mock es un valor de 3 dígitos configurable
- Sin credenciales bancarias, sin datos personales, sin APIs de pago reales
- `authorization_code` siempre con prefijo `MOCK-`, para que sea evidente en los
  datos aguas abajo

### 9.2 Lo que sí hay que proteger

Aunque los pagos sean falsos, la infraestructura es real:

| Riesgo | Mitigación |
|---|---|
| Clave privada del dispositivo en el repositorio | `secrets.h` en `.gitignore`; `*.pem` ya está cubierto |
| Certificado con permisos amplios | Política de IoT acotada a los dos topics de ese `pos_id` |
| API abierta al mundo | Throttling en API Gateway; sin datos sensibles que filtrar |
| Web estática en S3 | Sólo lectura pública del sitio; `block_public_*` en el resto del bucket |
| Lambda con permisos de más | Sólo `iot:Publish` en el topic concreto |
| Wi-Fi del evento comprometida | TLS mutuo: un atacante en la red no puede publicar sin el certificado |

### 9.3 El certificado, en la práctica

Un solo *thing* con un certificado, generado a mano antes de la charla. No hace
falta *fleet provisioning*: es un dispositivo.

### 9.4 Deriva del reloj

El RTC del Core2 es bueno, pero si se queda sin batería pierde la hora y los
eventos llegarían con `timestamp` incorrecto. **Sincronizar por NTP al arrancar**,
y si falla, dejar que Firehose use su propia hora de ingesta como respaldo.

---

## 10. Consideraciones de coste

Estimación para una demo de 2 horas con ~50 eventos.

| Servicio | Uso | Coste |
|---|---|---|
| AWS IoT Core | ~150 mensajes + ~50 ejecuciones de regla | **$0** (nivel gratuito: 150 ejecuciones/día durante 12 meses) |
| Amazon Data Firehose | 50 × 5 KB = 250 KB | **~$0** (sin cargo horario) |
| API Gateway HTTP API | ~100 peticiones | **~$0** ($1.00 por millón) |
| Lambda | ~100 invocaciones, 128 MB | **$0** (nivel gratuito) |
| S3 sitio estático | < 1 MB, decenas de peticiones | **< $0.01** |
| S3 zona raw | 250 KB | **< $0.01** |
| **Total del componente** | | **< $0.05** |

Sumado al coste actual del workshop (~$0.50), sigue por debajo de $1.

### 10.1 Lo que encarecería esto

- **Kinesis Data Streams en lugar de Firehose:** +$0.08 por las 2 horas, y sin
  nivel gratuito. Con el stream olvidado encendido, ~$0.96/día
- **Dejar el certificado y el *thing*:** $0. No cuestan por existir
- **REST API en lugar de HTTP API:** ~3.5× más caro por petición, irrelevante a
  este volumen pero gratis de evitar

### 10.2 Coste del hardware

El M5Stack Core2 v1.3 ronda los **$50–60**. Es un gasto único y no recurrente,
pero conviene decirlo: **el workshop deja de ser reproducible sin comprar
hardware**. Ver §11.3.

---

## 11. Plan de contingencia

El mayor riesgo no es técnico: es **el Wi-Fi de la sala**.

### 11.1 Nivel 1 — MQTT falla, HTTPS funciona

Si IoT Core no conecta (puerto 8883 bloqueado, que en redes de evento pasa), el
POS cae a:

```
M5Stack → HTTPS POST → API Gateway → Lambda → IoT Core (publica en su nombre)
```

El resto del pipeline no cambia. **Implementar los dos caminos desde el
principio**, con un flag en el firmware. Es poco trabajo extra y elimina el
riesgo más probable.

### 11.2 Nivel 2 — Sin Wi-Fi

El POS funciona en local y **encola los eventos en la SD**. Se muestra la
interacción física, se explica el pipeline con el diagrama, y los eventos se
envían después. La demo pierde el efecto en vivo pero no se cae.

Alternativa mejor: **compartir Internet desde el móvil**. Configurar ese SSID
como red secundaria en el firmware.

### 11.3 Nivel 3 — Sin hardware

Un script `scripts/simulate_pos_events.py` que publica los mismos eventos con el
mismo esquema. Sirve para:

- Ensayar sin el dispositivo
- Que **cualquiera reproduzca el workshop sin comprar un M5Stack**
- Los tests de CI

**Este script no es opcional.** Sin él, el componente físico convierte un
workshop reproducible en una demo que sólo tú puedes dar.

### 11.4 Nivel 4 — Sin nada

Capturas y una grabación de la secuencia completa. Como el resto de los labs.

---

## 12. Secuencia de la demo

~5 minutos. El §32 la describe; aquí está con los tiempos y el problema del
buffer resuelto.

| # | Acción | Tiempo | Qué ve el público |
|---|---|---|---|
| 1 | Mostrar el POS encendido | 15 s | Un dispositivo físico, no un slide |
| 2 | Seleccionar Americano + Croissant | 20 s | `S/ 15.50` en pantalla |
| 3 | Tocar [ ▣ QR ] | 5 s | QR en pantalla. **Se publica `ORDER_CREATED`** |
| 4 | Pedir a alguien del público que lo escanee | 30 s | Participación real |
| 5 | La web se abre en su móvil | 15 s | Importe y `transaction_id` |
| 6 | Pulsa [ PAGAR ] | 10 s | ✓ PAYMENT APPROVED |
| 7 | **El POS cambia solo** y vibra | 10 s | El círculo se cierra. Es el momento de la demo |
| 8 | Mostrar el evento en IoT Core (MQTT test client) | 30 s | El JSON normalizado |
| 9 | *Aquí entra el buffer de 60 s de Firehose* | — | Se explica el esquema mientras |
| 10 | Mostrar el objeto en `raw/events/` | 30 s | El evento ya está en el data lake |
| 11 | Ejecutar el Glue job | 60 s | Calidad y transformación, como en el Lab 2 |
| 12 | Mostrar `curated/` y `rejected/` | 30 s | Parquet y el evento inválido apartado |
| 13 | Preguntar al asistente sobre la venta | 45 s | De un café a una respuesta de IA |

**El paso 9 es el truco.** En lugar de esperar mirando la pantalla, ese minuto se
usa para explicar el esquema del evento (§5), que es contenido de valor. La
espera desaparece.

Si se usa tarjeta en lugar de QR: pasos 3-7 se sustituyen por mostrar la tarjeta
ficticia, teclear el CVV y ver `AUTHORIZING → APPROVED`. El resto es idéntico, y
ese es justamente el punto del §26.

---

## 13. Impacto en la charla, y una decisión que no es mía

La charla son **30 minutos** ya repartidos:

| Bloque | Ahora | Con Smart Coffee |
|---|---|---|
| 1 — Problema + casos | 5 min | 5 min |
| 2 — Framework + arquitectura | 5 min | 5 min |
| 3 — Los tres labs | 15 min | **¿15 min + 5?** |
| 4 — Revelación + cierre | 5 min | 5 min |

**Los 5 minutos del Smart Coffee tienen que salir de algún sitio.** Tres opciones:

| Opción | Qué implica |
|---|---|
| **A — Sustituir la demo del Lab 1** | El POS *es* la ingesta. DMS pasa a mención conceptual, como propone el §29. **Es la más limpia y la que recomiendo:** no alarga la charla y refuerza el mensaje de que la plataforma empieza en el evento de negocio |
| **B — Recortar el Lab 3** | El RAG pasa a 3 minutos. Se pierde la demostración de las tres capas de memoria, que es lo más diferencial del Lab 3 |
| **C — Charla de 45 minutos** | Si el formato del evento lo permite. Hay que confirmarlo con la organización |

**Recomiendo A.** El §29 ya apunta ahí: el Lab 1 pasa a demostrar dos velocidades
de ingesta (histórica por DMS, tiempo real por IoT), y sólo la segunda se
ejecuta en vivo. La narrativa mejora y el tiempo cuadra.

---

## 14. Plan de implementación propuesto

**Nada de esto está hecho.** Orden sugerido, de menos a más riesgo:

| Fase | Qué | Depende de |
|---|---|---|
| 1 | `scripts/simulate_pos_events.py` + esquema de eventos | — |
| 2 | Terraform: IoT Core, Firehose, `raw/events/` | Fase 1 para probarlo |
| 3 | Pipeline de Glue para eventos (`curated/events/`) + reglas nuevas | Fase 2 |
| 4 | Lambda `payment-mock` + API Gateway | — |
| 5 | Web de pago QR en S3 | Fase 4 |
| 6 | Firmware del M5Stack | Fases 2 y 4; **necesita el hardware** |
| 7 | Documentos derivados para el RAG (ventas de Smart Coffee) | Fase 3 |
| 8 | Ensayo y capturas de respaldo | Todo |

**Las fases 1-5 y 7 no necesitan el M5Stack.** Se puede construir y probar el
pipeline entero con el simulador mientras el hardware llega, y eso reduce el
riesgo de que el dispositivo sea un bloqueante.

---

## Preguntas abiertas

Antes de implementar, hacen falta cuatro decisiones:

| # | Pregunta | Recomendación |
|---|---|---|
| SC-01 | ¿Firehose o Kinesis Data Streams? | **Firehose**: $0/hora y sin consumidor que escribir. El contrapeso es el buffer de 60 s, resuelto en §12 |
| SC-02 | ¿Pipeline de eventos separado o fusionado con el de transacciones? | **Separado** (`raw/events/` → `curated/events/`), para no romper la calibración ya probada del Lab 2 |
| SC-03 | ¿De dónde salen los 5 minutos de la charla? | **Opción A**: el POS sustituye la demo del Lab 1 |
| SC-04 | ¿Ya tienes el M5Stack Core2, y es v1.3? | Las v1.0 y v1.1 están EOL. Las fases 1-5 avanzan sin él |

---

## Fuentes

- [M5Stack Core2 — documentación oficial](https://docs.m5stack.com/en/core/core2) · [Core2 v1.3](https://docs.m5stack.com/en/core/Core2_v1.3)
- [Ejemplo de QR en M5Core2](https://github.com/m5stack/M5Core2/blob/master/examples/Advanced/QRCode/QRCode.ino)
- [Conectar ESP32 a AWS IoT Core con MQTT](https://how2electronics.com/connecting-esp32-to-amazon-aws-iot-core-using-mqtt/)
- [AWS IoT Core — acciones de regla](https://docs.aws.amazon.com/iot/latest/developerguide/iot-rule-actions.html) · [acción Firehose](https://docs.aws.amazon.com/iot/latest/developerguide/kinesis-firehose-rule-action.html)
- [Precios de AWS IoT Core](https://aws.amazon.com/iot-core/pricing/)
- [Precios de Kinesis Data Streams](https://aws.amazon.com/kinesis/data-streams/pricing/)
- [Precios de Amazon Data Firehose](https://aws.amazon.com/firehose/pricing/)
