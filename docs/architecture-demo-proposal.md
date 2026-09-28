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

> **Revisión del 2026-09-27.** Esta sección se reescribió al encontrar el
> repositorio oficial del **AWS IoT Kit** en local
> (`../Core2-for-AWS-IoT-EduKit`). Cambia tres premisas: el hardware es una
> variante distinta, el framework no es Arduino, y la gestión de la clave privada
> desaparece. Los apartados corregidos están marcados.

### 1.0 El hardware ya está, y no es el que propuse

El repositorio local `Core2-for-AWS-IoT-EduKit` revela que el dispositivo es el
**M5Stack Core2 for AWS IoT Kit** (antes *AWS IoT EduKit*), no el Core2 genérico.

La diferencia importa: incluye un **elemento seguro Microchip ATECC608
Trust&GO** que el Core2 v1.3 de tienda **no tiene**.

Y hay evidencia de que está aprovisionado:

```
Blinky-Hello-World/.pio/build/core2foraws-device_reg          ← registro compilado
utilities/AWS_IoT_registration_helper/output_files/
    012370911fd7211101_manifest.json                          ← serial del dispositivo
    device_cert.pem  signer_cert.crt  signer_key.pem
```

**Esto resuelve SC-04 y elimina el mayor riesgo del plan**: el hardware existe,
tiene serial `012370911fd7211101`, y ya pasó por el registration helper.

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

> **Corregido.** Ya no hace falta comprar nada: tienes el *Core2 for AWS IoT
> Kit*, que además del hardware anterior lleva el ATECC608. Si algún día
> necesitaras un segundo dispositivo, **pide la variante «for AWS IoT Kit»**, no
> el Core2 v1.3 genérico: sin el elemento seguro habría que gestionar la clave
> privada en el firmware, que es justo lo que este hardware evita.

### 1.2 Generación de QR — corregido: aún más simple

**No es Arduino.** El AWS IoT Kit usa **ESP-IDF**, así que `M5.Lcd.qrcode()` (de
la librería Arduino `M5Core2`) no aplica. Lo que hay es mejor: un componente
`qrcode` ya incluido en el proyecto, con una única función.

```c
#include "qrcode.h"

esp_err_t qrcode_display(const char *text);   // eso es toda la API
```

Está en `components/qrcode/` de los proyectos del kit. No hace falta el módulo
*M5Module-QRCode*: ese es un **lector**, y aquí sólo necesitamos **mostrar**.

**Decisión de diseño que se deriva de esto:** el QR debe contener **una URL
corta**, no el JSON de la transacción. Una versión 6 admite ~134 caracteres
alfanuméricos; el JSON del §24 de la especificación ocupa más, obligaría a una
versión alta, y un QR denso en una pantalla de 2 pulgadas **no se escanea con un
móvil desde el público**. La web recupera los detalles con el `transaction_id`
que viene en la URL.

### 1.3 Conectividad con AWS IoT Core — resuelta, y mejor de lo previsto

Corregido: no es `PubSubClient` sobre Arduino, sino el **AWS IoT Device SDK for
Embedded C** portado por Espressif (`esp-aws-iot`), sobre ESP-IDF y FreeRTOS.

Y la clave privada **no existe en el firmware**. De `Blinky-Hello-World/main/main.c`:

```c
mqttInitParams.pRootCALocation       = (const char *)aws_root_ca_pem_start;
mqttInitParams.pDeviceCertLocation   = "#";     // slot del ATECC608
mqttInitParams.pDevicePrivateKeyLocation = "#0"; // slot del ATECC608

char *client_id = malloc(CLIENT_ID_LEN + 1);
Atecc608_GetSerialString(client_id);            // identidad = serial del chip
```

Esos `#` y `#0` son referencias PKCS#11 a slots del elemento seguro. La clave se
generó **dentro del chip** y no se puede extraer. En `main/certs/` sólo hay
`aws-root-ca.pem`, que es público.

**Consecuencia para el §35 de la especificación:** el apartado de gestión de
secretos en el firmware desaparece. No hay `secrets.h`, no hay clave que proteger,
no hay nada que añadir al `.gitignore`. La identidad del dispositivo es el
hardware.

### 1.4 El proyecto de referencia cubre la mayor parte

`Blinky-Hello-World` ya trae, funcionando y sobre este hardware:

| Pieza | Estado |
|---|---|
| Aprovisionamiento por elemento seguro | ✅ |
| Conexión MQTT a AWS IoT Core (TLS mutuo) | ✅ |
| Publicar y suscribirse con QoS 0 y 1 | ✅ |
| Reconexión automática (`aws_iot_mqtt_attempt_reconnect`) | ✅ |
| Wi-Fi (`wifi.c`) | ✅ |
| UI con **LVGL** (`ui.c`) | ✅ |
| Tarea FreeRTOS para periféricos | ✅ |

Lo que hay que escribir es la lógica del POS: catálogo, carrito, pantallas de
pago y el payload del evento. **La parte difícil —identidad, TLS, MQTT— está
hecha.**

> **LVGL cambia el diseño de la UI a mejor.** No hay que dibujar píxeles: hay
> objetos, botones con callbacks y layouts. Una UI de POS con lista de productos y
> carrito es trabajo normal de GUI, no de gráficos a bajo nivel.

### 1.5 Riesgo real: el toolchain está congelado en 2021

`platformio.ini` fija `platform = espressif32@3.2.0` y el README pide **ESP-IDF
v4.2**. Son versiones de 2021. ESP-IDF va por la v5.x.

No es un impedimento —los proyectos compilan, hay evidencia en `.pio/build`—
pero conviene saberlo:

- **No actualizar** el toolchain «porque hay versión nueva». El BSP
  `core2forAWS` y el port de `esp-cryptoauthlib` están atados a esa versión
- Reservar tiempo si hay que reinstalar el entorno en otra máquina
- El programa pasó de AWS a M5Stack (la documentación vive ahora en
  `aws-iot-kit-docs.m5stack.com` y se renombró de *EduKit* a *AWS IoT Kit*), así
  que no esperar actualizaciones

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
| POS físico | **Core2 for AWS IoT Kit** + ESP-IDF v4.2 / PlatformIO | **Real** | Sí |
| UI del POS | **LVGL** sobre el BSP `core2forAWS` | **Real** | Sí |
| Generación de QR | componente `qrcode` (`qrcode_display()`) | **Real** | Sí |
| Web de pago QR | HTML/CSS/JS estático en S3 | **Real** | Sí |
| Procesador de pagos | Lambda `payment-mock` | **Mock** | Sí |
| Autorización de tarjeta | Lambda, validación de CVV de prueba | **Mock** | Sí |
| API | API Gateway HTTP API | **Real** | Sí |
| Transporte de eventos | AWS IoT Core, MQTT + TLS mutuo con **ATECC608** | **Real** | Sí |
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

### 6.1 Topics MQTT — ajustado al patrón del kit

El ejemplo del kit usa el **serial del elemento seguro** como `clientId` y como
raíz del topic:

```c
snprintf(subscribe_topic,     "%s/#", client_id);   // client_id = serial ATECC608
snprintf(base_publish_topic,  "%s/",  client_id);
```

Conviene respetarlo, porque la política de IoT que creó el registration helper ya
está acotada a ese patrón. Propuesta:

```
012370911fd7211101/events      ← el POS publica     (QoS 1)
012370911fd7211101/results     ← el POS se suscribe (QoS 1)
```

**Implicación para el esquema de eventos:** `pos_id` no es un valor inventado,
es el serial del dispositivo. Mantener `pos_id: "POS-01"` como alias legible y
añadir `device_id` con el serial, para que el dato sea trazable al hardware:

```json
"pos_id": "POS-01",
"device_id": "012370911fd7211101"
```

La regla de IoT puede enriquecer el evento con el `clientId` usando la función
`clientid()` de su SQL, así que **el firmware no tiene ni que enviarlo**.

### 6.2 Estructura del firmware — corregida

Partiendo de `Blinky-Hello-World`, que es el ejemplo del kit más cercano, con la
convención de ESP-IDF (`main/` + `components/`):

```
firmware/smart-coffee-pos/
├── platformio.ini          # copiado de Blinky-Hello-World: espressif32@3.2.0
├── sdkconfig.defaults
├── main/
│   ├── CMakeLists.txt
│   ├── main.c              # app_main, tareas FreeRTOS, máquina de estados
│   ├── ui.c / ui.h         # pantallas LVGL: catálogo, carrito, pago, resultado
│   ├── catalog.c           # productos y precios (en firmware: no hay inventario)
│   ├── payment.c           # HTTPS al mock de tarjeta (esp_http_client)
│   ├── events.c            # construir y publicar el payload del evento
│   ├── wifi.c              # reutilizado tal cual del ejemplo
│   └── certs/
│       └── aws-root-ca.pem # público; es el ÚNICO certificado en el repositorio
├── components/
│   ├── core2forAWS/        # BSP, incluye atecc608/ y esp-cryptoauthlib
│   └── qrcode/             # qrcode_display()
└── README.md
```

> **No hay `secrets.h`, y esto es lo mejor del hardware.** La clave privada vive
> en el ATECC608 y no se puede extraer; el firmware la referencia por slot
> (`"#0"`). Lo único que se versiona es la Root CA de Amazon, que es pública.
>
> Comparado con un ESP32 genérico, esto elimina de golpe la categoría de riesgo
> «clave privada filtrada en el repositorio».

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
| Clave privada del dispositivo en el repositorio | **No aplica con este hardware.** La clave vive en el ATECC608 y no se puede extraer. Sólo se versiona la Root CA, que es pública |
| Certificado con permisos amplios | Política de IoT acotada al patrón `<serial>/*`, que es lo que ya creó el registration helper |
| API abierta al mundo | Throttling en API Gateway; sin datos sensibles que filtrar |
| Web estática en S3 | Sólo lectura pública del sitio; `block_public_*` en el resto del bucket |
| Lambda con permisos de más | Sólo `iot:Publish` en el topic concreto |
| Wi-Fi del evento comprometida | TLS mutuo: un atacante en la red no puede publicar sin el certificado |

### 9.3 El certificado, en la práctica — ya resuelto

Nada que hacer: el registration helper del kit ya generó el manifest
(`012370911fd7211101_manifest.json`) y registró el *thing*. No hace falta *fleet
provisioning* ni generar certificados a mano.

### 9.3.1 Aviso sobre el repositorio del AWS IoT Kit

Al revisar ese repositorio encontré algo que conviene arreglar, y **no lo toqué
porque no es este proyecto**:

```
Blinky-Hello-World/utilities/AWS_IoT_registration_helper/output_files/
    signer_key.pem          ← clave privada EC, SIN ignorar por git
    device_cert.pem
    signer_cert.crt
    012370911fd7211101_manifest.json
```

`signer_key.pem` empieza por `-----BEGIN EC PRIVATE KEY-----` y
`git check-ignore` confirma que **no está ignorado**: un `git add -A` en ese
repositorio la commitearía.

La regla existe en el `.gitignore` de la raíz pero no encaja, por dos motivos:

```
/utilities/device_registration_helper/output_files/*
 ↑ancla en la raíz                ↑el directorio real se llama AWS_IoT_registration_helper
```

Arreglo, en el `.gitignore` de ese repositorio:

```
**/AWS_IoT_registration_helper/output_files/
**/*_manifest.json
```

Si esa clave llegó a estar en un commit, **rotarla**: quitarla del historial no la
invalida.

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

### 10.2 Coste del hardware — ya cubierto

El dispositivo ya está, así que para esta charla el coste de hardware es **$0**.

Sigue siendo cierto lo importante: **quien reproduzca el workshop desde GitHub no
tendrá un Core2 for AWS IoT Kit** (~$50–60, y la variante con elemento seguro es
más difícil de encontrar que el Core2 normal). Por eso el simulador del §11.3 no
es opcional.

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
| 6 | Firmware del POS, partiendo de `Blinky-Hello-World` | Fases 2 y 4. **El hardware ya está y está aprovisionado** |
| 7 | Documentos derivados para el RAG (ventas de Smart Coffee) | Fase 3 |
| 8 | Ensayo y capturas de respaldo | Todo |

**Las fases 1-5 y 7 no necesitan el M5Stack.** Se puede construir y probar el
pipeline entero con el simulador, y eso mantiene el workshop reproducible para
quien no tenga el dispositivo.

La fase 6 es más barata de lo que parecía: `Blinky-Hello-World` ya resuelve
identidad, TLS, MQTT, Wi-Fi y el arranque de LVGL. Lo que queda es la lógica de
negocio del POS.

> **Antes de la fase 6, comprobar que el entorno compila.** El toolchain está
> fijado en ESP-IDF v4.2 / `espressif32@3.2.0` (§1.5). Hay evidencia de builds
> previos en `.pio/build`, así que funcionaba; conviene confirmarlo antes de
> depender de ello.

---

## Preguntas abiertas

Antes de implementar, hacen falta cuatro decisiones:

| # | Pregunta | Recomendación |
|---|---|---|
| SC-01 | ¿Firehose o Kinesis Data Streams? | **Firehose**: $0/hora y sin consumidor que escribir. El contrapeso es el buffer de 60 s, resuelto en §12 |
| SC-02 | ¿Pipeline de eventos separado o fusionado con el de transacciones? | **Separado** (`raw/events/` → `curated/events/`), para no romper la calibración ya probada del Lab 2 |
| SC-03 | ¿De dónde salen los 5 minutos de la charla? | **Opción A**: el POS sustituye la demo del Lab 1 |
| SC-04 | ~~¿Ya tienes el M5Stack Core2?~~ | **Resuelta.** Es un *Core2 for AWS IoT Kit* con ATECC608, serial `012370911fd7211101`, ya aprovisionado |
| SC-05 | ¿Compila hoy el entorno del kit? (ESP-IDF v4.2, de 2021) | Comprobarlo antes de la fase 6. Si no, la fase 6 se retrasa pero las 1-5 no |

---

## Fuentes

**Código de referencia, en local** — `../Core2-for-AWS-IoT-EduKit/`:
`Blinky-Hello-World/` (elemento seguro + MQTT + LVGL),
`components/core2forAWS/atecc608/`, `components/qrcode/`,
`Smart-Thermostat/` (device shadow).

- [AWS IoT Kit — documentación](https://aws-iot-kit-docs.m5stack.com) · [Cloud Connected Blinky](https://aws-iot-kit-docs.m5stack.com/en/blinky-hello-world/)
- [Core2 for AWS IoT Kit — tienda M5Stack](https://m5stack.com/products/m5stack-core2-esp32-iot-development-kit-for-aws-iot-kit)
- [M5Stack Core2 — documentación del hardware](https://docs.m5stack.com/en/core/core2) · [Core2 v1.3](https://docs.m5stack.com/en/core/Core2_v1.3)
- [esp-aws-iot — port de Espressif del AWS IoT Device SDK](https://github.com/espressif/esp-aws-iot)
- [AWS IoT Core — acciones de regla](https://docs.aws.amazon.com/iot/latest/developerguide/iot-rule-actions.html) · [acción Firehose](https://docs.aws.amazon.com/iot/latest/developerguide/kinesis-firehose-rule-action.html)
- [Precios de AWS IoT Core](https://aws.amazon.com/iot-core/pricing/)
- [Precios de Kinesis Data Streams](https://aws.amazon.com/kinesis/data-streams/pricing/)
- [Precios de Amazon Data Firehose](https://aws.amazon.com/firehose/pricing/)
