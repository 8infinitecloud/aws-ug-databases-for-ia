/* Cliente de la API de adquirencia. */
#pragma once

#include "esp_err.h"
#include "pos.h"

/* Genera una referencia nueva para el carrito. */
void pos_api_new_reference(char *out, size_t len);

/* POST /v1/intents — cierra el carrito. */
esp_err_t pos_api_create_intent(pos_cart_t *cart);

/* POST /v1/intents/{ref}/confirm con canal CARD_PRESENT. */
esp_err_t pos_api_confirm_card(const char *ref, const char *cvv, pos_charge_t *out);

/*
 * GET /v1/intents/{ref} — para el flujo QR.
 *
 * El POS no sabe cuando alguien paga desde el movil, asi que pregunta. El
 * camino alternativo seria escuchar el topic de IoT Core, pero eso obliga a
 * mantener la conexion MQTT abierta mientras se muestra el QR.
 */
esp_err_t pos_api_poll_intent(const char *ref, char *status_out, size_t status_len);

/* GET /v1/intents/{ref} cuando ya esta pagado, para leer el cobro. */
esp_err_t pos_api_fetch_charge(const char *ref, pos_charge_t *out);

/* La URL que va dentro del QR. */
void pos_api_payment_url(const char *ref, char *out, size_t len);
