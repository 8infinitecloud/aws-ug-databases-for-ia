# Terminal POS — M5Stack Core2 for AWS IoT Kit

El firmware del terminal que cobra. Compila **en local**: no hay pipeline, no
hay nada en la nube que construya esto.

> **DEMO EDUCATIVA — no se procesan pagos reales.**
> El terminal no lee tarjetas: no hay lector de banda ni de chip, el PAN no
> existe en ninguna parte del firmware, y el CVV que se teclea es un valor de
> prueba (`123` por defecto). La autorización la simula el backend.
>
> Lo único real de la identidad del dispositivo es la clave privada, que vive en
> el elemento seguro ATECC608 y no se puede extraer.

---

## Arrancar

```bash
cd payments/firmware
./setup.sh                # trae los componentes del kit (~20 MB)
pio run                   # compilar
pio run -t upload -t monitor   # grabar y ver el log
```

`setup.sh --local` los copia de un clon del kit que ya tengas, que es más
rápido si ya lo tienes al lado.

Configuración (`pio run -t menuconfig`, menú **FinCorp POS**):

| Opción | Qué es |
|---|---|
| `WIFI_SSID` / `WIFI_PASSWORD` | la red del local |
| `API_BASE_URL` | el endpoint de API Gateway, sin barra final |
| `TERMINAL_ID` | el TID; tiene que existir en `acquiring.terminals` |
| `DEMO_CVV` | el CVV que aprueba. DEMO: cualquier otro declina con `05` |

`API_BASE_URL` sale de `terraform -chdir=infra/payments output api_endpoint`.

---

## Lo que hace

```
Catálogo  ──[COBRAR]──>  Método  ──┬──[TARJETA]──>  CVV  ──>  Resultado
                                   └──[QR]──────>  QR    ──>  Resultado
```

| Fichero | Qué resuelve |
|---|---|
| `main.c` | arranque: inicializa el kit, lanza la UI y luego la red |
| `pos_ui.c` | las cuatro pantallas, en LVGL 7.11 |
| `pos_api.c` | el cliente HTTP contra la API de adquirencia |
| `pos_qr.c` | dibuja el QR en la pantalla |
| `wifi.c` | conexión, con reintentos |

### La pantalla del QR tiene que poder abandonarse

Encontrado usando el terminal, no leyendo el código: **la pantalla del QR no
tenía ningún botón**. Quien entraba se quedaba atrapado los 120 s del timeout,
con el QR anterior en pantalla y sin forma de empezar otra venta.

El síntoma no parecía eso. Parecía que la página de pago "cacheaba" el cobro
anterior: el móvil volvía a escanear el QR viejo —el único que había— y veía una
operación ya pagada, mientras el POS seguía sondeando una referencia que nadie
iba a pagar.

Ahora la pantalla lleva **CANCELAR**, y la tarea de sondeo comprueba la bandera
antes de cada consulta. Volver al catálogo también cancela cualquier sondeo
vivo: si no, la tarea de la venta anterior podía pintar su resultado encima de
la nueva.

La bandera es `volatile` porque la escribe el hilo de LVGL y la lee una tarea en
otro core. Sin eso el compilador puede cachearla en un registro y la tarea no se
entera nunca.

### El canvas del QR se fugaba

LVGL borra el objeto canvas pero **no libera el buffer externo** que se le pasó.
Cada QR dejaba ~54 KB de PSRAM sin recuperar. Con varias ventas seguidas eso
acaba agotándola y el QR deja de dibujarse **sin error visible**: simplemente no
aparece. `pos_qr_free()` lo libera, y `limpiar()` lo llama antes de borrar los
objetos.

### Tres decisiones que no son obvias

**La UI arranca antes que el WiFi.** Si se conectara primero, la pantalla
quedaría negra durante la conexión y parecería que el terminal está muerto.

**Las llamadas a la API van en su propia tarea.** LVGL no es reentrante y todo
lo que toca la pantalla va entre `xSemaphoreTake(xGuiSemaphore)` y su `Give`.
Una petición HTTPS tarda cientos de milisegundos: hacerla en el hilo de LVGL
congelaría la pantalla justo cuando el cliente está mirando.

**El flujo QR sondea en vez de escuchar MQTT.** El POS no se entera de que
alguien pagó desde el móvil. Mantener abierta la conexión a IoT Core mientras se
muestra el QR cuesta memoria, y una reconexión a mitad de la venta sería peor
que preguntar cada dos segundos.

---

## El QR: lo que hay que saber antes de la demo

El kit trae un componente `qrcode` con `qrcode_display()`. **No sirve aquí**:
hace `printf` del QR como arte ASCII en la consola serie. Está pensado para
aprovisionar un dispositivo mirando el log.

Lo que se usa es la librería que hay debajo, **qrcodegen** (Nayuki, MIT), y el
mapa de módulos se pinta sobre un canvas de LVGL.

**El QR es de acercarse, no de escanear desde lejos.** Con la URL real de API
Gateway (69 caracteres) sale un QR versión 4, 33×33 módulos, a 4 px por módulo.
En una pantalla de 2″ a ~200 ppp eso son **21 mm** reales, con módulos de
0,63 mm: la distancia de lectura práctica ronda los **25 cm**.

Para la charla significa que alguien sube y escanea, lo cual además se ve mejor
en cámara. Si hiciera falta a distancia, la palanca es acortar la URL con un
dominio propio, no agrandar la pantalla.

`tests/test_firmware.py` fija esto: si alguien alarga la URL o la referencia, el
test falla antes de que el QR se vuelva ilegible en la charla.

---

## El toolchain, y por qué las versiones van clavadas

El kit es de 2021 y usa ESP-IDF v4.2. **Verificado el 2026-09-28**: compila hoy
sin tocar nada.

| | |
|---|---|
| PlatformIO Core | 6.2.0 |
| `espressif32` | 3.2.1 |
| `framework-espidf` | 3.40201.210513 (ESP-IDF v4.2.1) |
| Tiempo de compilación | ~45 s en caliente, ~56 s en frío |
| Tamaño | 1,0 MB · 16 % del flash · 0,9 % de la RAM |

Por eso `platformio.ini` no lleva rangos de versión: una demo tiene que compilar
igual el día de la charla que hoy.

---

## Los componentes no están en este repositorio

`setup.sh` los trae de
[m5stack/Core2-for-AWS-IoT-EduKit](https://github.com/m5stack/Core2-for-AWS-IoT-EduKit),
clavados al commit `3ec44e0`:

| Componente | Qué aporta |
|---|---|
| `core2forAWS` | pantalla, táctil, PMU, LVGL 7.11 y el driver del ATECC608 |
| `esp-aws-iot` | el cliente de IoT Core |
| `esp-cryptoauthlib` | el acceso al elemento seguro |
| `qrcodegen` | la generación de QR, extraída de `Getting-Started` |

Son 20 MB de código de AWS y M5Stack, iguales para todo el mundo. Versionarlos
aquí duplicaría código de terceros y engordaría el clon del taller sin aportar
nada.

---

## Estado

**Grabado y arrancando en el dispositivo** (2026-09-28, Core2 con ESP32-D0WDQ6-V3).

Lo verificado con el hardware delante:

| | |
|---|---|
| Grabado | 1.048.496 bytes, hash verificado |
| Arranque | `Project name: FinCorp_POS`, PSRAM de 4 MB inicializada |
| Pantalla | ILI9341 en orientación LANDSCAPE |
| **ATECC608** | inicializado, zona de datos `locked`, tipo `60 03` |
| UI | `pos_ui: interfaz lista` |
| Táctil | responde y acierta los botones |
| Flujo | catálogo → producto → COBRAR → QR → crea la intención |
| WiFi | reintenta 8 veces y cae en el camino de error diseñado |

**Un solo toque genera siete eventos de LVGL** —PRESSED, PRESSING ×2,
SHORT_CLICKED, CLICKED, RELEASED y alguno más—. El filtro por
`LV_EVENT_CLICKED` de los callbacks es lo que hace que un toque produzca
exactamente **una** intención de cobro y no siete. Comprobado instrumentando los
callbacks en el dispositivo.

### El flujo completo, contra la API real

Verificado el 2026-09-28 con el terminal conectado a un hotspot de móvil y
`payments` desplegado en AWS:

```
+ 4.0s  IP: 172.20.10.3
+21.4s  POST /v1/intents -> 201                    (se toca QR)
+25.3s  GET  /v1/intents/PIEF4950CA -> 200         (empieza el sondeo)
        … cada ~3,6 s …
+42.0s  POST /v1/intents/PIEF4950CA/confirm -> 200 (alguien pagó)
```

El cobro llegó entero a Aurora:

```
PIEF4950CA · SUCCEEDED · 38,50 PEN · Smart Coffee
Espresso 8,00 + Latte 18,50 + Croissant 12,00 = 38,50
```

Las líneas suman exactamente el importe, así que el carrito del POS y el
validador del backend concuerdan. El backend sirvió la página del QR al móvil
(`GET /pay/PIEF4950CA` 200) y publicó el evento en IoT Core, en el topic que
cuelga del serial real del elemento seguro:

```
evento payment.captured publicado en 012370911fd7211101/events
```

El segundo `confirm` que manda el POS al detectar `SUCCEEDED` devolvió el cobro
existente en vez de cobrar otra vez (`intencion ya estaba cobrada`): la
idempotencia funcionando sobre hardware real.

### Configurar la red y la API

Las credenciales van en `sdkconfig`, que **está en `.gitignore`**. No las pongas
en `Kconfig.projbuild`, que sí se commitea.

```bash
pio run -t menuconfig     # menú "FinCorp POS"
```

`API_BASE_URL` sale de `terraform -chdir=infra/payments output -raw api_endpoint`.
