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

Compila y el binario está verificado. **No se ha grabado en el dispositivo
todavía**, así que el comportamiento en pantalla —posiciones, tamaños de fuente,
si el táctil cae donde debe— está sin comprobar. Es lo primero que hay que hacer
con el Core2 delante.
