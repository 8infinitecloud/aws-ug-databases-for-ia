/*
 * Terminal POS de FinCorp — punto de entrada.
 *
 * DEMO EDUCATIVA. Este terminal no procesa pagos reales. No lee tarjetas: no
 * hay lector de banda ni de chip, no existe el PAN en ninguna parte del
 * firmware, y el CVV que se teclea es un valor de prueba. La autorizacion la
 * simula el backend, que responde con codigos de ISO 8583 de verdad sobre una
 * decision inventada.
 *
 * Lo que si es real: la identidad del dispositivo. La clave privada vive en el
 * elemento seguro ATECC608 y no se puede extraer.
 */

#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"

#include "core2forAWS.h"

#include "pos_telemetry.h"
#include "pos_ui.h"
#include "wifi.h"

static const char *TAG = "main";

void app_main(void)
{
    ESP_LOGI(TAG, "FinCorp POS — DEMO, no se procesan pagos reales");

    /* Inicializa PMU, pantalla, tactil, ATECC608 y el resto del kit. */
    Core2ForAWS_Init();
    Core2ForAWS_Display_SetBrightness(80);

    pos_ui_start();

    /* La UI arranca antes que la red para que la pantalla no quede en negro
     * durante la conexion: el POS dice que esta conectando. */
    if (wifi_connect() != ESP_OK) {
        ESP_LOGE(TAG, "sin WiFi: el terminal no puede cobrar");
    }
    pos_ui_set_wifi(wifi_is_connected());

    /* Despues de la red: el latido la necesita, y si no la hay espera. */
    pos_telemetry_start();
}
