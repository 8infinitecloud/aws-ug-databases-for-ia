/*
 * Telemetria del terminal: un latido cada CONFIG_POS_TELEMETRY_INTERVAL_S.
 *
 * El contrato del mensaje es el mismo que publica la flota virtual del
 * generador de trafico (scripts/generate_traffic.py, EstadoTerminal.latido).
 * Si cambia aqui, cambia alli: el Lab 4 y Athena leen los dos por igual.
 *
 * Conectar, publicar y desconectar en cada latido, en vez de mantener MQTT
 * abierto. Es la misma razon por la que el QR sondea por HTTP en vez de
 * escuchar MQTT (pos_ui.c): una conexion TLS permanente cuesta memoria todo el
 * rato, y una reconexion en mitad de una venta es peor que un latido tardio.
 * Un handshake cada 30 s en reposo es barato; el terminal no tiene prisa.
 *
 * Los contadores son DELTAS desde el latido anterior, no acumulados: se suman
 * en Athena sin restar filas consecutivas, y un reinicio no da saltos
 * negativos.
 */

#include <stdio.h>
#include <string.h>

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "esp_system.h"
#include "esp_timer.h"
#include "esp_wifi.h"
#include "esp_heap_caps.h"
#include "cJSON.h"

#include "core2forAWS.h"
#include "pos_telemetry.h"
#include "wifi.h"

#if CONFIG_POS_TELEMETRY_ENABLED
#include "aws_iot_config.h"
#include "aws_iot_mqtt_client_interface.h"
#endif

static const char *TAG = "pos_telemetry";

#define TELEMETRY_SCHEMA   "1.0"
#define FIRMWARE_VERSION   "smart-coffee-pos 0.2.0"

/* La tension de una LiPo de una celda: 4,15 V llena, 3,30 V es donde el AXP192
 * corta. Lineal entre las dos: no es exacto —la curva real es plana en medio—,
 * pero para saber si un terminal va a morir esta tarde basta. */
#define BAT_FULL_V   4.15f
#define BAT_EMPTY_V  3.30f

/* ── Contadores compartidos ──────────────────────────────────────────────── */
/* Los escriben la tarea del cobro y el hilo de LVGL, en el otro core. Un
 * spinlock y no un mutex: las secciones son de unas pocas instrucciones. */
static portMUX_TYPE mux = portMUX_INITIALIZER_UNLOCKED;

static struct {
    uint32_t http_requests, http_errors, http_timeouts;
    int64_t  latency_sum_ms, latency_max_ms;
    uint32_t qr_shown, qr_paid, qr_canceled, qr_expired;
} delta;

static volatile bool busy = false;

void pos_telemetry_note_http(int status, int64_t elapsed_ms, bool timeout)
{
    portENTER_CRITICAL(&mux);
    delta.http_requests++;
    if (status == 0 || status >= 500) {
        delta.http_errors++;
    }
    if (timeout) {
        delta.http_timeouts++;
    }
    delta.latency_sum_ms += elapsed_ms;
    if (elapsed_ms > delta.latency_max_ms) {
        delta.latency_max_ms = elapsed_ms;
    }
    portEXIT_CRITICAL(&mux);
}

void pos_telemetry_note_qr(pos_qr_event_t ev)
{
    portENTER_CRITICAL(&mux);
    switch (ev) {
        case POS_QR_SHOWN:    delta.qr_shown++;    break;
        case POS_QR_PAID:     delta.qr_paid++;     break;
        case POS_QR_CANCELED: delta.qr_canceled++; break;
        case POS_QR_EXPIRED:  delta.qr_expired++;  break;
    }
    portEXIT_CRITICAL(&mux);
}

void pos_telemetry_set_busy(bool b)
{
    busy = b;
}

#if CONFIG_POS_TELEMETRY_ENABLED

/* La CA raiz de Amazon: la misma que valida API Gateway valida IoT Core (ATS). */
extern const uint8_t aws_root_ca_pem_start[] asm("_binary_aws_root_ca_pem_start");

static char serial[ATCA_SERIAL_NUM_SIZE * 2 + 1];

/* Construye el mensaje y vacia los deltas. Devuelve un string de cJSON que el
 * llamante libera. */
static char *build_message(void)
{
    typeof(delta) d;
    portENTER_CRITICAL(&mux);
    d = delta;
    memset(&delta, 0, sizeof(delta));
    portEXIT_CRITICAL(&mux);

    float v = Axp192_GetBatVolt();
    int pct = (int)((v - BAT_EMPTY_V) / (BAT_FULL_V - BAT_EMPTY_V) * 100.0f + 0.5f);
    pct = pct < 0 ? 0 : (pct > 100 ? 100 : pct);

    wifi_ap_record_t ap;
    int rssi = esp_wifi_sta_get_ap_info(&ap) == ESP_OK ? ap.rssi : 0;

    cJSON *m = cJSON_CreateObject();
    cJSON_AddStringToObject(m, "type", "heartbeat");
    cJSON_AddStringToObject(m, "schema_version", TELEMETRY_SCHEMA);
    cJSON_AddStringToObject(m, "terminal_id", CONFIG_TERMINAL_ID);
    cJSON_AddStringToObject(m, "firmware_version", FIRMWARE_VERSION);
    cJSON_AddNumberToObject(m, "uptime_s", (double)(esp_timer_get_time() / 1000000));
    cJSON_AddNumberToObject(m, "battery_pct", pct);
    cJSON_AddNumberToObject(m, "battery_v", (double)((int)(v * 100)) / 100.0);
    /* Con el USB enchufado el VBUS esta por encima de 4 V. */
    cJSON_AddBoolToObject(m, "charging", Axp192_GetVbusVolt() > 4.0f);
    if (rssi != 0) {
        cJSON_AddNumberToObject(m, "rssi_dbm", rssi);
    } else {
        cJSON_AddNullToObject(m, "rssi_dbm");
    }
    cJSON_AddNumberToObject(m, "free_heap_bytes", esp_get_free_heap_size());
    cJSON_AddNumberToObject(m, "free_psram_bytes",
                            heap_caps_get_free_size(MALLOC_CAP_SPIRAM));
    cJSON_AddNumberToObject(m, "http_requests", d.http_requests);
    cJSON_AddNumberToObject(m, "http_errors", d.http_errors);
    cJSON_AddNumberToObject(m, "http_timeouts", d.http_timeouts);
    if (d.http_requests > 0) {
        cJSON_AddNumberToObject(m, "http_latency_ms_avg",
                                (double)(d.latency_sum_ms / d.http_requests));
        cJSON_AddNumberToObject(m, "http_latency_ms_max", (double)d.latency_max_ms);
    } else {
        cJSON_AddNullToObject(m, "http_latency_ms_avg");
        cJSON_AddNullToObject(m, "http_latency_ms_max");
    }
    cJSON_AddNumberToObject(m, "qr_shown", d.qr_shown);
    cJSON_AddNumberToObject(m, "qr_paid", d.qr_paid);
    cJSON_AddNumberToObject(m, "qr_canceled", d.qr_canceled);
    cJSON_AddNumberToObject(m, "qr_expired", d.qr_expired);

    char *out = cJSON_PrintUnformatted(m);
    cJSON_Delete(m);
    return out;
}

/* Conecta, publica un mensaje con QoS 1 y desconecta. */
static IoT_Error_t publish_once(const char *topic, const char *payload)
{
    AWS_IoT_Client client;
    IoT_Client_Init_Params init = iotClientInitParamsDefault;
    IoT_Client_Connect_Params conn = iotClientConnectParamsDefault;

    init.enableAutoReconnect = false;
    init.pHostURL = CONFIG_AWS_IOT_MQTT_HOST;
    init.port = CONFIG_AWS_IOT_MQTT_PORT;
    init.pRootCALocation = (const char *)aws_root_ca_pem_start;
    /* '#' le dice al port de mbedTLS que el certificado y la clave estan en el
     * ATECC608 (slot 0). La clave privada nunca sale del chip. */
    init.pDeviceCertLocation = "#";
    init.pDevicePrivateKeyLocation = "#0";
    init.mqttCommandTimeout_ms = 10000;
    init.tlsHandshakeTimeout_ms = 8000;
    init.isSSLHostnameVerify = true;

    IoT_Error_t rc = aws_iot_mqtt_init(&client, &init);
    if (rc != SUCCESS) {
        return rc;
    }

    conn.keepAliveIntervalInSec = 30;
    conn.isCleanSession = true;
    conn.MQTTVersion = MQTT_3_1_1;
    /* El clientId es el serial: es lo que la politica de IoT del dispositivo
     * exige, y lo que ata el mensaje al hardware. */
    conn.pClientID = serial;
    conn.clientIDLen = (uint16_t)strlen(serial);
    conn.isWillMsgPresent = false;

    rc = aws_iot_mqtt_connect(&client, &conn);
    if (rc != SUCCESS) {
        aws_iot_mqtt_free(&client);
        return rc;
    }

    IoT_Publish_Message_Params msg = {
        .qos = QOS1,
        .isRetained = 0,
        .payload = (void *)payload,
        .payloadLen = strlen(payload),
    };
    rc = aws_iot_mqtt_publish(&client, topic, (uint16_t)strlen(topic), &msg);

    aws_iot_mqtt_disconnect(&client);
    aws_iot_mqtt_free(&client);
    return rc;
}

static void telemetry_task(void *arg)
{
    (void)arg;

    if (Atecc608_GetSerialString(serial) != ATCA_SUCCESS) {
        ESP_LOGE(TAG, "no se pudo leer el serial del ATECC608: sin telemetria");
        vTaskDelete(NULL);
        return;
    }
    char topic[64];
    snprintf(topic, sizeof(topic), "%s/telemetry", serial);
    ESP_LOGI(TAG, "latido cada %d s en %s", CONFIG_POS_TELEMETRY_INTERVAL_S, topic);

    for (;;) {
        vTaskDelay(pdMS_TO_TICKS(CONFIG_POS_TELEMETRY_INTERVAL_S * 1000));

        /* Una venta en curso tiene prioridad. Se espera como mucho un minuto:
         * un QR que nadie paga no puede silenciar el latido para siempre, o el
         * Lab 4 veria el terminal como caido cuando solo esta esperando. */
        for (int i = 0; busy && i < 60; i++) {
            vTaskDelay(pdMS_TO_TICKS(1000));
        }
        if (!wifi_is_connected()) {
            continue;
        }

        char *payload = build_message();
        if (payload == NULL) {
            continue;
        }
        IoT_Error_t rc = publish_once(topic, payload);
        if (rc == SUCCESS) {
            ESP_LOGI(TAG, "latido publicado (%d bytes)", (int)strlen(payload));
        } else {
            /* Se pierde este latido; los deltas ya se vaciaron y no se
             * reenvian. Un terminal que no puede publicar es justo lo que el
             * Lab 4 tiene que ver como un hueco. */
            ESP_LOGW(TAG, "latido no publicado: error %d", rc);
        }
        cJSON_free(payload);
    }
}

void pos_telemetry_start(void)
{
    /* 8 KB: el handshake TLS con el elemento seguro es lo que mas pila pide. */
    xTaskCreatePinnedToCore(telemetry_task, "telemetria", 8192, NULL, 2, NULL, 1);
}

#else

void pos_telemetry_start(void)
{
    ESP_LOGI(TAG, "telemetria desactivada (menuconfig: FinCorp POS > Telemetria)");
}

#endif
